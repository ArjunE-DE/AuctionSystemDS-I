import requests

SERVERS = [
    "http://localhost:8001",
    "http://localhost:8002",
    "http://localhost:8003"
]

def get_server():
    """Return the first alive server from the cluster"""
    for server in SERVERS:
        try:
            r = requests.get(f"{server}/health", timeout=0.5)
            if r.status_code == 200:
                return server
        except:
            continue
    raise RuntimeError("No server is alive!")

def create_auction():
    data = {
        "id": "",
        "name": input("Name: "),
        "description": input("Description: "),
        "starting_price": float(input("Start price: "))
    }

    server = get_server()
    r = requests.post(f"{server}/auction", json=data)
    print(r.json())

def list_auctions():
    server = get_server()
    r = requests.get(f"{server}/auctions")
    for a in r.json():
        print(a)

def place_bid():
    auction_id = input("Auction ID: ")
    amount = float(input("Bid amount: "))
    bidder = input("Your name: ")

    server = get_server()
    r = requests.post(
        f"{server}/bid/{auction_id}",
        json={"bidder": bidder, "amount": amount}
    )
    print(r.json())

# ===== MENU =====
while True:
    print("\n1. Create auction")
    print("2. List auctions")
    print("3. Place bid")
    print("4. Exit")

    c = input("> ")

    if c == "1":
        create_auction()
    elif c == "2":
        list_auctions()
    elif c == "3":
        place_bid()
    else:
        break
