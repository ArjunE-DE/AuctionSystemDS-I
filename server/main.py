from fastapi import FastAPI, HTTPException, Request
from models import AuctionItem, Bid
from storage import storage
from replication import replicate_to_peers
from election import start_election, is_leader, leader_url, start_monitor
import uuid
import requests
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_election()
    start_monitor()
    yield

app = FastAPI(lifespan=lifespan)

# ===== CLIENT ENDPOINTS =====

@app.post("/auction")
async def create_auction(auction: AuctionItem, request: Request):
    if not is_leader():
        return requests.post(f"{leader_url}/auction", json=auction.dict()).json()

    auction.id = str(uuid.uuid4())
    storage.add_auction(auction)

    replicate_to_peers(
        origin=str(request.base_url).strip("/"),
        endpoint="/replicate/auction",
        data=auction.dict()
    )

    return {"status": "created", "id": auction.id}


@app.get("/auctions")
async def list_auctions():
    return storage.get_auctions()


@app.post("/bid/{auction_id}")
async def place_bid(auction_id: str, bid: Bid, request: Request):
    if not is_leader():
        return requests.post(f"{leader_url}/bid/{auction_id}", json=bid.dict()).json()

    auction = storage.get_auction(auction_id)

    if not auction:
        raise HTTPException(status_code=404, detail="Auction not found")

    if bid.amount <= auction.highest_bid:
        raise HTTPException(status_code=400, detail="Bid too low")

    auction.highest_bid = bid.amount
    auction.bids.append(bid)

    storage.update_auction(auction)

    replicate_to_peers(
        origin=str(request.base_url).strip("/"),
        endpoint="/replicate/bid",
        data={
            "auction_id": auction_id,
            "bid": bid.dict()
        }
    )

    return {"status": "bid accepted"}


# ===== SERVER-TO-SERVER REPLICATION =====

@app.post("/replicate/auction")
async def replicate_auction(auction: AuctionItem):
    storage.add_auction(auction)
    return {"status": "replicated"}


@app.post("/replicate/bid")
async def replicate_bid(data: dict):
    auction = storage.get_auction(data["auction_id"])

    if auction:
        bid = Bid(**data["bid"])
        auction.highest_bid = bid.amount
        auction.bids.append(bid)
        storage.update_auction(auction)

    return {"status": "replicated"}


# ===== HEALTH & LEADER ENDPOINTS =====

@app.get("/health")
def health():
    return {"status": "alive"}

@app.post("/leader")
def set_leader(data: dict):
    global leader_url
    leader_url = data["leader"]
    print(f"[Leader Update] Leader set to: {leader_url}")  # <- add this
    return {"status": "ok"}
