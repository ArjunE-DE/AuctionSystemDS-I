from fastapi import FastAPI, HTTPException, Request
from models import AuctionItem, Bid
from storage import storage
from replication import replicate_to_peers
import uuid

app = FastAPI()

# ===== CLIENT ENDPOINTS =====

@app.post("/auction")
async def create_auction(auction: AuctionItem, request: Request):
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
