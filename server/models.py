from pydantic import BaseModel
from typing import List

class Bid(BaseModel):
    bidder: str
    amount: float

class AuctionItem(BaseModel):
    id: str
    name: str
    description: str
    starting_price: float
    highest_bid: float = 0.0
    bids: List[Bid] = []
