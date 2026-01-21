import socket
import threading
import json
import random
import time

# ---------------------------
# CONFIG
# ---------------------------
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 5007

client_id = random.randint(1, 1000)

# ---------------------------
# SOCKETS
# ---------------------------
mcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
mcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
mcast_sock.bind(('', MULTICAST_PORT))
mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton('0.0.0.0')
mcast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_sock.bind(('', 0))

discovered_servers = {}  # {server_id: (ip, port)}
chosen_server = None

# ---------------------------
# DISCOVERY
# ---------------------------
def discover_servers():
    while True:
        data, addr = mcast_sock.recvfrom(1024)
        try:
            msg = json.loads(data.decode())
            if msg.get("type") == "discovery":
                discovered_servers[msg["server_id"]] = (addr[0], msg["port"])
        except:
            continue

def choose_server():
    global chosen_server
    while not discovered_servers:
        print("Waiting for servers on the network...")
        time.sleep(1)
    chosen_server = random.choice(list(discovered_servers.values()))
    print(f"Connected to server at {chosen_server[0]}:{chosen_server[1]}")

# ---------------------------
# SEND MESSAGES
# ---------------------------
def send_create_auction(item_id, name, start_price):
    msg = json.dumps({"type": "create_auction", "item": {"id": item_id, "name": name, "last_bid": start_price, "bidder": None}})
    server_sock.sendto(msg.encode(), chosen_server)
    try:
        data, _ = server_sock.recvfrom(4096)
        reply = json.loads(data.decode())
        print(reply.get("msg"))
    except:
        print("No response from server")

def send_bid(item_id, bidder, bid):
    msg = json.dumps({"type": "bid", "item_id": item_id, "bidder": bidder, "bid": bid})
    server_sock.sendto(msg.encode(), chosen_server)
    try:
        data, _ = server_sock.recvfrom(4096)
        reply = json.loads(data.decode())
        print(reply.get("msg"))
    except:
        print("No response from server")

def get_auction_update():
    msg = json.dumps({"type": "get_auctions"})
    server_sock.sendto(msg.encode(), chosen_server)
    try:
        data, _ = server_sock.recvfrom(4096)
        msg = json.loads(data.decode())
        if msg.get("type") == "auction_update":
            print("\n--- Auction Update ---")
            for item in msg["data"].values():
                start = time.strftime('%H:%M:%S', time.gmtime(item["start_time"]))
                end = time.strftime('%H:%M:%S', time.gmtime(item["end_time"]))
                print(f"{item['id']}: {item['name']} - {item['last_bid']} by {item['bidder']} (Start: {start}, End: {end})")
            print("---------------------\n")
    except Exception as e:
        print(f"[ERROR] Failed to get auction update: {e}")

# ---------------------------
# MAIN
# ---------------------------
if __name__ == "__main__":
    threading.Thread(target=discover_servers, daemon=True).start()
    choose_server()

    while True:
        print("1. Create auction\n2. Bid\n3. Get Auction Update\n4. Exit")
        choice = input("Choose action: ")
        if choice == "1":
            item_id = input("Item ID: ")
            name = input("Item Name: ")
            try:
                start_price = float(input("Start Price: "))
            except:
                print("Invalid start price")
                continue
            send_create_auction(item_id, name, start_price)
        elif choice == "2":
            item_id = input("Item ID: ")
            bidder = input("Your name: ")
            try:
                bid = float(input("Bid amount: "))
            except:
                print("Invalid bid amount")
                continue
            send_bid(item_id, bidder, bid)
        elif choice == "3":
            get_auction_update()
        elif choice == "4":
            break
