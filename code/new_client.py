import socket
import struct
import json
import random
import threading
import time
from datetime import datetime
from queue import Queue

# ------------------------------
# Config
# ------------------------------
MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5000
CLIENT_PORT = random.randint(6000, 7000)

KNOWN_SERVERS = set()
SERVER_LIST = []
SERVER_LAST_SEEN = {}  # port -> timestamp
RR_INDEX = 0

# server port -> server IP
SERVER_IPS = {}

SESSION_ID = None

MESSAGE_QUEUE = Queue()
INPUT_QUEUE = Queue()

REQUEST_COUNTER = 0

# ------------------------------
# States
# ------------------------------
STATE_LOGIN_USERNAME = 1
STATE_LOGIN_PASSWORD = 2
STATE_NEW_USER_PROMPT = 3
STATE_NEW_USER_USERNAME = 4
STATE_NEW_USER_PASSWORD = 5
STATE_NORMAL = 6

state = STATE_LOGIN_USERNAME
pending_new_user_message = None
temp_username = None

# ------------------------------
# UDP socket
# ------------------------------
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", CLIENT_PORT))
print(f"[CLIENT] Running on port {CLIENT_PORT}")

# ------------------------------
# Multicast listener
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
            data, addr = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") != "HELLO":
            continue

        port = int(msg["server_port"])
        ip = addr[0]

        KNOWN_SERVERS.add(port)
        SERVER_LAST_SEEN[port] = time.time()
        SERVER_IPS[port] = ip
        SERVER_LIST = sorted(KNOWN_SERVERS)

threading.Thread(target=multicast_listener, daemon=True).start()

# ------------------------------
# Prune dead servers
# ------------------------------
def prune_dead_servers():
    global SERVER_LIST
    TIMEOUT = 8
    while True:
        now = time.time()
        removed = False
        for port in list(SERVER_LIST):
            last = SERVER_LAST_SEEN.get(port, 0)
            if now - last > TIMEOUT:
                print(f"[CLIENT] Pruning dead server {port} from list.")
                SERVER_LIST.remove(port)
                KNOWN_SERVERS.discard(port)
                SERVER_LAST_SEEN.pop(port, None)
                SERVER_IPS.pop(port, None)
                removed = True
        if removed and not SERVER_LIST:
            print("[CLIENT] No known servers after pruning.")
        time.sleep(2)

threading.Thread(target=prune_dead_servers, daemon=True).start()

# ------------------------------
# Server response listener
# ------------------------------
def listen_responses():
    while True:
        try:
            data, _ = sock.recvfrom(4096)
            msg = json.loads(data.decode())
            MESSAGE_QUEUE.put(msg)
        except:
            continue

threading.Thread(target=listen_responses, daemon=True).start()

# ------------------------------
# User input thread
# ------------------------------
def user_input_thread():
    while True:
        line = input()
        INPUT_QUEUE.put(line)

threading.Thread(target=user_input_thread, daemon=True).start()

# ------------------------------
# Round-robin server selection
# ------------------------------
def choose_server():
    global RR_INDEX
    if not SERVER_LIST:
        return None
    server = SERVER_LIST[RR_INDEX % len(SERVER_LIST)]
    RR_INDEX += 1
    return server

# ------------------------------
# Request ID generator
# ------------------------------
def next_request_id():
    global REQUEST_COUNTER
    REQUEST_COUNTER += 1
    return f"{CLIENT_PORT}-{REQUEST_COUNTER}"

# ------------------------------
# Safe send to any server
# ------------------------------
def send_to_server_safe(command):
    global SESSION_ID

    cmd = dict(command)
    if SESSION_ID:
        cmd["session_id"] = SESSION_ID

    request_id = next_request_id()

    tried = set()
    ports_to_try = []

    fallback = choose_server()
    if fallback is not None:
        ports_to_try.append(fallback)

    for port in ports_to_try:
        if port is None or port in tried:
            continue
        tried.add(port)

        msg = {
            "type": "CLIENT",
            "client_port": CLIENT_PORT,
            "request_id": request_id,
            "command": cmd
        }

        ip = SERVER_IPS.get(port, "127.0.0.1")

        try:
            sock.sendto(json.dumps(msg).encode(), (ip, port))
        except:
            if port in SERVER_LIST:
                print(f"[CLIENT] Failed to send to server {port}. Removing from list.")
                SERVER_LIST.remove(port)
            continue

        time.sleep(0.2)
        if not MESSAGE_QUEUE.empty():
            return True

        if port in SERVER_LIST:
            print(f"[CLIENT] Server {port} unresponsive. Removing from server list.")
            SERVER_LIST.remove(port)

    print("[CLIENT] No servers responded.")
    return False

# ------------------------------
# Display auctions
# ------------------------------
def display_auction_list(items):
    if not items:
        print("[AUCTIONS] No active auctions.\n")
        return

    print("\n[AUCTIONS]")
    print(f"{'ID':<3} {'Name':<15} {'Owner':<10} {'Current Bid':<12} "
          f"{'Last Bidder':<12} {'Start Time':<20} {'End Time':<20}")

    for item in items:
        start_time = datetime.fromtimestamp(item["start_time"])
        end_time = datetime.fromtimestamp(item["end_time"])
        print(f"{item['id']:<3} {item['name']:<15} {item['owner']:<10} "
              f"{item['current_bid']:<12} {str(item.get('last_bidder','-')):<12} "
              f"{start_time.strftime('%Y-%m-%d %H:%M:%S'):<20} "
              f"{end_time.strftime('%Y-%m-%d %H:%M:%S'):<20}")
    print()

# ------------------------------
# Prompt login username
# ------------------------------
def prompt_login_username():
    global state
    while not INPUT_QUEUE.empty():
        INPUT_QUEUE.get()
    print("Login required:")
    print("Username:")
    state = STATE_LOGIN_USERNAME

# ------------------------------
# Wait for servers
# ------------------------------
print("[CLIENT] Discovering servers...")
while not SERVER_LIST:
    time.sleep(0.5)
print(f"[CLIENT] Known servers: {SERVER_LIST}\n")

prompt_login_username()

# ------------------------------
# MAIN LOOP
# ------------------------------
while True:

    # ------------------------------
    # Process server messages
    # ------------------------------
    while not MESSAGE_QUEUE.empty():
        msg = MESSAGE_QUEUE.get()

        if msg["type"] == "RESPONSE":
            resp = msg["data"]

            if isinstance(resp, list):
                display_auction_list(resp)
                continue

            if isinstance(resp, dict) and resp.get("status") == "new_user":
                pending_new_user_message = resp["message"]
                state = STATE_NEW_USER_PROMPT
                while not INPUT_QUEUE.empty():
                    INPUT_QUEUE.get()
                continue

            if isinstance(resp, dict) and resp.get("status") == "success":
                if "Logged in" in resp.get("message", "") or "created" in resp.get("message", ""):
                    SESSION_ID = resp["session_id"]
                    print(f"\n[CLIENT] {resp['message']} (session_id: {SESSION_ID})\n")
                    state = STATE_NORMAL
                    continue

            if isinstance(resp, dict) and resp.get("status") == "bid too low":
                print(f"\n[BID REJECTED] {resp.get('message')}\n")
                continue
            
            # # Auction won
            if isinstance(resp, dict) and resp.get("status") == "auction won":
                if resp.get("winner_session_id") == SESSION_ID:
                    print(f"\n[AUCTION WON] {resp.get('message')}\n")
                else:
                    print(f"\n[AUCTION ENDED] {resp.get('message')}\n")
                continue

            if isinstance(resp, dict) and resp.get("status") == "error":
                print("\n[SERVER RESPONSE]")
                print(json.dumps(resp, indent=2))
                if state in (STATE_NEW_USER_PASSWORD, STATE_LOGIN_PASSWORD):
                    prompt_login_username()
                continue

    # ------------------------------
    # Process user input
    # ------------------------------
    while not INPUT_QUEUE.empty():
        cmd = INPUT_QUEUE.get().strip()

        if state == STATE_LOGIN_USERNAME:
            temp_username = cmd
            print("Password:")
            state = STATE_LOGIN_PASSWORD
            continue

        elif state == STATE_LOGIN_PASSWORD:
            send_to_server_safe({
                "action": "login",
                "username": temp_username,
                "password": cmd
            })
            continue

        elif state == STATE_NEW_USER_PROMPT:
            if pending_new_user_message is not None:
                print(pending_new_user_message)
                print("Create new user? (yes/no):")
                pending_new_user_message = None

            if cmd.lower() == "yes":
                while not INPUT_QUEUE.empty():
                    INPUT_QUEUE.get()
                print("Choose username:")
                state = STATE_NEW_USER_USERNAME
            elif cmd.lower() == "no":
                prompt_login_username()
            else:
                print("Please type 'yes' or 'no':")
            continue

        elif state == STATE_NEW_USER_USERNAME:
            temp_username = cmd
            while not INPUT_QUEUE.empty():
                INPUT_QUEUE.get()
            print("Choose password:")
            state = STATE_NEW_USER_PASSWORD
            continue

        elif state == STATE_NEW_USER_PASSWORD:
            send_to_server_safe({
                "action": "register",
                "username": temp_username,
                "password": cmd
            })
            continue

        elif state == STATE_NORMAL:
            if not cmd:
                continue

            parts = cmd.split()
            if not parts:
                continue

            action = parts[0].lower()
            args = parts[1:]
            command = None

            if action == "list":
                if args:
                    print("Usage: list\n")
                    continue
                command = {"action": "list"}

            elif action == "add":
                if len(args) < 4:
                    print("Usage: add <name> <description> <duration> <start_price>\n")
                    continue

                name = args[0]
                description = args[1]

                try:
                    duration = int(args[2])
                    start_price = float(args[3])
                except ValueError:
                    print("Duration must be an integer, start_price must be a number.\n")
                    continue

                command = {
                    "action": "add",
                    "name": name,
                    "description": description,
                    "duration": duration,
                    "start_price": start_price
                }

            elif action == "bid":
                if len(args) != 2:
                    print("Usage: bid <item_id> <amount>\n")
                    continue

                try:
                    item_id = int(args[0])
                    amount = float(args[1])
                except ValueError:
                    print("Item ID must be an integer, amount must be a number.\n")
                    continue

                command = {
                    "action": "bid",
                    "item_id": item_id,
                    "bid": amount
                }

            elif action == "quit":
                print("Goodbye.")
                exit(0)

            else:
                print("Unknown command. Available commands:")
                print("  list")
                print("  add <name> <description> <duration> <start_price>")
                print("  bid <item_id> <amount>")
                print("  quit\n")
                continue

            send_to_server_safe(command)
            time.sleep(0.1)
            send_to_server_safe({"action": "list"})

    time.sleep(0.05)
