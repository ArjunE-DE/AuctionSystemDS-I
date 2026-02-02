import socket
import struct
import json
import random
import threading
import time
from datetime import datetime

# ------------------------------
# Config
# ------------------------------
MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5000
CLIENT_PORT = random.randint(6000, 7000)

KNOWN_SERVERS = set()
SERVER_LIST = []
RR_INDEX = 0
AUTHENTICATED = False
LAST_LIST = []

# ------------------------------
# UDP socket
# ------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", CLIENT_PORT))
print(f"[CLIENT] Running on port {CLIENT_PORT}")

# ------------------------------
# Multicast listener (dynamic server discovery)
# ------------------------------
mcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
mcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
mcast_sock.bind(("", MCAST_PORT))
mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
mcast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

def multicast_listener():
    global SERVER_LIST
    while True:
        try:
            data, _ = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") != "HELLO":
            continue

        port = int(msg["server_port"])
        KNOWN_SERVERS.add(port)
        SERVER_LIST = sorted(KNOWN_SERVERS)

# ------------------------------
# Listen for server responses
# ------------------------------
def listen_responses():
    global AUTHENTICATED, LAST_LIST
    while True:
        try:
            data, _ = sock.recvfrom(4096)
        except (ConnectionResetError, OSError):
            continue
        except Exception:
            continue

        try:
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") != "RESPONSE":
            continue

        resp = msg["data"]

        if isinstance(resp, list):
            LAST_LIST = resp
        elif isinstance(resp, dict):
            if resp.get("status") == "success" and "Logged in" in resp.get("message", ""):
                AUTHENTICATED = True
                print(f"\n[CLIENT] {resp['message']}\n")
            elif resp.get("status") == "error" and "Not authenticated" in resp.get("message", ""):
                AUTHENTICATED = False
                print(f"\n[CLIENT] {resp['message']}\n")
            else:
                print("\n[SERVER RESPONSE]")
                print(json.dumps(resp, indent=2))
                print()
        else:
            print("\n[SERVER RESPONSE]")
            print(resp)
            print()

        if AUTHENTICATED and LAST_LIST:
            display_auction_list()

# ------------------------------
# Choose a server (round-robin + failover)
# ------------------------------
def choose_server():
    global RR_INDEX
    if not SERVER_LIST:
        return None
    for _ in range(len(SERVER_LIST)):
        server = SERVER_LIST[RR_INDEX % len(SERVER_LIST)]
        RR_INDEX += 1
        return server
    return None

# ------------------------------
# Safe send to server
# ------------------------------
def send_to_server_safe(msg):
    server_port = choose_server()
    if server_port is None:
        print("[CLIENT] No servers available")
        return False
    try:
        sock.sendto(json.dumps(msg).encode(), ("127.0.0.1", server_port))
        return True
    except (ConnectionResetError, OSError):
        # Server down, remove from list and retry next time
        print(f"[CLIENT] Server {server_port} down, removing from list")
        SERVER_LIST.remove(server_port)
        if server_port in KNOWN_SERVERS:
            KNOWN_SERVERS.remove(server_port)
        return False
    except Exception as e:
        print(f"[CLIENT] Error sending to server {server_port}: {e}")
        return False

# ------------------------------
# Display auctions nicely
# ------------------------------
def display_auction_list():
    if not LAST_LIST:
        print("[AUCTIONS] No active auctions.\n")
        return
    print("\n[AUCTIONS]")
    print(f"{'ID':<3} {'Name':<15} {'Owner':<10} {'Current Bid':<12} {'Last Bidder':<12} {'Start Time':<20} {'End Time':<20}")
    for item in LAST_LIST:
        start_time = datetime.fromtimestamp(item.get("start_time", time.time()))
        end_time = datetime.fromtimestamp(item.get("end_time", time.time()))
        print(f"{item['id']:<3} {item['name']:<15} {item['owner']:<10} "
              f"{item['current_bid']:<12} {str(item.get('last_bidder','-')):<12} "
              f"{start_time.strftime('%Y-%m-%d %H:%M:%S'):<20} "
              f"{end_time.strftime('%Y-%m-%d %H:%M:%S'):<20}")
    print()

# ------------------------------
# Start threads
# ------------------------------
threading.Thread(target=multicast_listener, daemon=True).start()
threading.Thread(target=listen_responses, daemon=True).start()

# ------------------------------
# Wait for servers
# ------------------------------
print("[CLIENT] Discovering servers...")
while not SERVER_LIST:
    time.sleep(0.5)
print(f"[CLIENT] Known servers: {SERVER_LIST}\n")

# ------------------------------
# Login phase
# ------------------------------
while not AUTHENTICATED:
    print("Login required:")
    username = input("Username: ").strip()
    password = input("Password: ").strip()
    command = {"action":"login","username":username,"password":password}
    msg = {"type":"CLIENT","client_port":CLIENT_PORT,"command":command}
    while not send_to_server_safe(msg):
        time.sleep(0.5)
    while not AUTHENTICATED:
        time.sleep(0.5)

# ------------------------------
# Auction interface
# ------------------------------
print("\nWelcome to the Auction!")
print("Commands:")
print("  list")
print("  add <name> <description> <duration_seconds> <start_price>")
print("  bid <item_id> <amount>")
print("  quit\n")

while True:
    cmd = input("> ").strip()
    if not cmd:
        continue
    if cmd == "quit":
        break

    parts = cmd.split()
    command = None

    if parts[0] == "list":
        command = {"action":"list"}
    elif parts[0] == "add" and len(parts) >= 5:
        command = {
            "action":"add",
            "name":parts[1],
            "description":parts[2],
            "duration":int(parts[3]),
            "start_price":float(parts[4])
        }
    elif parts[0] == "bid" and len(parts) == 3:
        command = {"action":"bid","item_id":int(parts[1]),"bid":float(parts[2])}
    else:
        print("Invalid command\n")
        continue

    msg = {"type":"CLIENT","client_port":CLIENT_PORT,"command":command}
    send_to_server_safe(msg)

    # Automatically refresh list after action
    time.sleep(0.3)
    list_msg = {"type":"CLIENT","client_port":CLIENT_PORT,"command":{"action":"list"}}
    send_to_server_safe(list_msg)