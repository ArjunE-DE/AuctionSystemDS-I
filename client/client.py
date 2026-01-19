import requests

SERVER = "http://localhost:8001"

def create_auction():
    data = {
        "id": "",
        "name": input("Name: "),
        "description": input("Description: "),
        "starting_price": float(input("Start price: "))
    }

    r = requests.post(f"{SERVER}/auction", json=data)
    print(r.json())


def list_auctions():
    r = requests.get(f"{SERVER}/auctions")
    for a in r.json():
        print(a)


def place_bid():
    auction_id = input("Auction ID: ")
    amount = float(input("Bid amount: "))
    bidder = input("Your name: ")

    r = requests.post(
        f"{SERVER}/bid/{auction_id}",
        json={"bidder": bidder, "amount": amount}
    )

    print(r.json())


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
