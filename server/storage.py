from models import AuctionItem

class Storage:
    def __init__(self):
        self.auctions = {}

    def add_auction(self, auction: AuctionItem):
        self.auctions[auction.id] = auction

    def get_auctions(self):
        return list(self.auctions.values())

    def get_auction(self, auction_id):
        return self.auctions.get(auction_id)

    def update_auction(self, auction: AuctionItem):
        self.auctions[auction.id] = auction

storage = Storage()
