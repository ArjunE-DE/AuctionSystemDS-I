# models.py
import uuid
import time
from dataclasses import dataclass, field
from typing import Dict, Optional
from config import dprint

@dataclass
class ClientInfo:
    id: str
    name: str
    password: str

@dataclass
class AuctionItem:
    id: str
    name: str
    description: str
    owner_id: str
    start_time: float
    end_time: float
    start_price: float
    current_bid: float
    status_open: bool
    buyer_id: Optional[str] = None
    password: str = ""

    def is_active(self) -> bool:
        return self.status_open and time.time() < self.end_time

    def close_if_expired(self) -> None:
        if self.status_open and time.time() >= self.end_time:
            self.status_open = False

@dataclass
class AuctionState:
    clients: Dict[str, ClientInfo] = field(default_factory=dict)
    items: Dict[str, AuctionItem] = field(default_factory=dict)

    def create_client(self, name: str, password: str) -> ClientInfo:
        cid = str(uuid.uuid4())
        client = ClientInfo(id=cid, name=name, password=password)
        self.clients[cid] = client
        return client

    def authenticate(self, name: str, password: str) -> Optional[ClientInfo]:
        for c in self.clients.values():
            if c.name == name and c.password == password:
                return c
        return None

    def retrieve_client_name(self, client_id: str) -> Optional[ClientInfo]:
        for c in self.clients.values():
            if c.id == client_id:
                return c.name
        return None

    def create_auction(self, owner_id: str, name: str, description: str,
                       start_price: float,
                       duration_seconds: float) -> AuctionItem:
        now = time.time()
        item_id = str(uuid.uuid4())
        item = AuctionItem(
            id=item_id,
            name=name,
            description=description,
            owner_id=owner_id,
            start_time=now,
            end_time=now + duration_seconds,
            start_price=start_price,
            current_bid=start_price,
            status_open=True,
        )
        self.items[item_id] = item
        return item

    def list_active_items(self):
        for item in self.items.values():
            item.close_if_expired()
        return [i for i in self.items.values() if i.is_active()]

    def list_my_purchases(self, buyer_id: str):
        for item in self.items.values():
            return [i for i in self.items.values() if not i.is_active() and i.buyer_id == buyer_id]

    # def place_bid(self, bidder_id: str, item_id: str, amount: float) -> bool:
    #     item = self.items.get(item_id)
    #     dprint(f"{self}")
    #     if item is None:
    #         return False
    #     item.close_if_expired()
    #     if not item.is_active():
    #         return False
    #     if amount <= item.current_bid:
    #         return False
    #     item.current_bid = amount
    #     item.buyer_id = bidder_id
    #     return True

    def place_bid(self, bidder_id: str, item_id: str, amount: float) -> bool:
        item = self.items.get(item_id)
        if item is None:
            return False
        item.close_if_expired()
        if not item.is_active():
            return False
        if amount <= item.current_bid:
            return False
        item.current_bid = amount
        item.buyer_id = bidder_id
        return True

