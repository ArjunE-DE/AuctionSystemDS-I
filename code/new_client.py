#!/usr/bin/env python3
import socket
import struct
import json
import random
import threading
import time
import sys
from datetime import datetime

# ---------------- CONFIG ----------------

MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5000

CLIENT_PORT = random.randint(6000, 7000)

KNOWN_SERVERS = set()
SERVER_LAST_SEEN = {}
SERVER_IPS = {}

LEADER_PORT = None
SESSION_ID = None

sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock.bind(("0.0.0.0", CLIENT_PORT))
sock.settimeout(1)

print(f"[CLIENT] Running on port {CLIENT_PORT}")

# ---------------- MULTICAST DISCOVERY ----------------

mcast = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
mcast.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
mcast.bind(("", MCAST_PORT))

mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
mcast.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)


def multicast_listener():
    while True:
        try:
            data, addr = mcast.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") != "HELLO":
            continue

        port = msg["server_port"]
        KNOWN_SERVERS.add(port)
        SERVER_LAST_SEEN[port] = time.time()
        SERVER_IPS[port] = addr[0]


threading.Thread(target=multicast_listener, daemon=True).start()

# ---------------- PRUNE DEAD SERVERS ----------------

def prune():
    global LEADER_PORT
    while True:
        now = time.time()
        for p in list(KNOWN_SERVERS):
            if now - SERVER_LAST_SEEN.get(p, 0) > 8:
                print(f"[CLIENT] Removing dead server {p}")
                KNOWN_SERVERS.discard(p)
                SERVER_LAST_SEEN.pop(p, None)
                SERVER_IPS.pop(p, None)
                if LEADER_PORT == p:
                    LEADER_PORT = None
        time.sleep(2)

threading.Thread(target=prune, daemon=True).start()

# ---------------- INPUT HELPERS ----------------

def read_int(prompt, allow_empty=False):
    while True:
        try:
            s = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled.")
            return None
        if allow_empty and s.strip() == "":
            return None
        try:
            return int(s.strip())
        except ValueError:
            print("Please enter a valid integer.")

def read_float(prompt, allow_empty=False):
    while True:
        try:
            s = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled.")
            return None
        if allow_empty and s.strip() == "":
            return None
        try:
            return float(s.strip())
        except ValueError:
            print("Please enter a valid number.")

def read_nonempty(prompt, allow_empty=False):
    while True:
        try:
            s = input(prompt)
        except (EOFError, KeyboardInterrupt):
            print("\nInput cancelled.")
            return None
        if allow_empty and s.strip() == "":
            return ""
        if s.strip() == "":
            print("Input cannot be empty.")
            continue
        return s.strip()

# ---------------- NETWORK HELPERS ----------------

def send_command(cmd):

    global LEADER_PORT

    ports = []
    if LEADER_PORT:
        ports.append(LEADER_PORT)
    ports += list(KNOWN_SERVERS)

    tried = set()

    for port in ports:
        if port in tried:
            continue
        tried.add(port)

        ip = SERVER_IPS.get(port, "127.0.0.1")

        envelope = {
            "type": "CLIENT",
            "client_port": CLIENT_PORT,
            "command": cmd
        }

        try:
            sock.sendto(json.dumps(envelope).encode(), (ip, port))
        except:
            continue

        try:
            data, _ = sock.recvfrom(8192)
            msg = json.loads(data.decode())
        except socket.timeout:
            continue

        if msg["type"] == "REDIRECT":
            LEADER_PORT = msg["leader_port"]
            return send_command(cmd)

        return msg

    print("No servers reachable.")
    return None


# ---------------- CLI ----------------

def login():
    global SESSION_ID

    u = read_nonempty("Username: ")
    if u is None:
        return
    p = read_nonempty("Password: ")
    if p is None:
        return

    resp = send_command({
        "action": "login",
        "username": u,
        "password": p
    })

    if resp and resp.get("type") == "RESPONSE":
        data = resp.get("data", {})
        if data.get("status") == "success":
            SESSION_ID = data["session_id"]
            print("Logged in.")
        else:
            print(data.get("message", data))
    else:
        print("Login failed or no response.")


def register():
    global SESSION_ID

    u = read_nonempty("Choose username: ")
    if u is None:
        return
    p = read_nonempty("Choose password: ")
    if p is None:
        return

    resp = send_command({
        "action": "register",
        "username": u,
        "password": p
    })

    if resp and resp.get("type") == "RESPONSE":
        data = resp.get("data", {})
        if data.get("status") == "success":
            SESSION_ID = data["session_id"]
            print("Registered.")
        else:
            print(data.get("message", data))
    else:
        print("Register failed or no response.")


def list_auctions():
    """
    Request the list of auctions from the server and print them with human-readable start/end times.
    """
    resp = send_command({
        "action": "list",
        "session_id": SESSION_ID
    })

    if not resp:
        print("Failed to list auctions or no response.")
        return

    if resp.get("type") != "RESPONSE":
        print("Unexpected response:", resp)
        return

    data = resp.get("data")
    # If server returned an error message (dict with status), print it
    if isinstance(data, dict) and data.get("status") == "error":
        print(data.get("message", data))
        return

    # Expecting a list of items
    if not isinstance(data, list):
        print("Unexpected data format:", data)
        return

    if not data:
        print("No active auctions.")
        return

    print("\n--- AUCTION ITEMS ---")
    now_ts = time.time()
    for item in data:
        # Safely extract timestamps; fall back to None if missing
        start_ts = item.get("start_time")
        end_ts = item.get("end_time")

        # Convert to readable strings if possible
        try:
            start_str = datetime.fromtimestamp(float(start_ts)).strftime("%Y-%m-%d %H:%M:%S") if start_ts is not None else "N/A"
        except Exception:
            start_str = "Invalid timestamp"

        try:
            end_str = datetime.fromtimestamp(float(end_ts)).strftime("%Y-%m-%d %H:%M:%S") if end_ts is not None else "N/A"
        except Exception:
            end_str = "Invalid timestamp"

        print(f"ID: {item.get('id')} | Name: {item.get('name')} | Current Bid: {item.get('current_bid')} | "
              f"Last Bidder: {item.get('last_bidder')} | Starts: {start_str} | Ends: {end_str}")
    print("---------------------\n")


def add():
    if SESSION_ID is None:
        print("You must be logged in to add an item.")
        return

    name = read_nonempty("Name: ")
    if name is None:
        print("Add cancelled.")
        return
    desc = input("Description: ")
    dur = read_int("Duration (seconds): ")
    if dur is None:
        print("Add cancelled.")
        return
    price = read_float("Start price: ")
    if price is None:
        print("Add cancelled.")
        return

    resp = send_command({
        "action": "add",
        "name": name,
        "description": desc,
        "duration": dur,
        "start_price": price,
        "session_id": SESSION_ID
    })

    if resp:
        print(resp)
    else:
        print("Add failed or no response.")


def bid():
    if SESSION_ID is None:
        print("You must be logged in to place a bid.")
        return

    i = read_int("Item id: ")
    if i is None:
        print("Bid cancelled.")
        return
    a = read_float("Amount: ")
    if a is None:
        print("Bid cancelled.")
        return

    resp = send_command({
        "action": "bid",
        "item_id": i,
        "bid": a,
        "session_id": SESSION_ID
    })

    if resp:
        print(resp)
    else:
        print("Bid failed or no response.")


# ---------------- MAIN ----------------

print("[CLIENT] Waiting for servers...")
while not KNOWN_SERVERS:
    time.sleep(0.5)

print("[CLIENT] Servers:", KNOWN_SERVERS)

while True:
    if SESSION_ID is None:
        cmd = input("\nlogin | register | quit > ").strip().lower()
        if cmd == "login":
            login()
        elif cmd == "register":
            register()
        elif cmd == "quit":
            sys.exit(0)
        else:
            print("Available commands: login, register, quit")
    else:
        cmd = input("\nlist | add | bid | quit > ").strip().lower()
        if cmd == "list":
            list_auctions()
        elif cmd == "add":
            add()
        elif cmd == "bid":
            bid()
        elif cmd == "quit":
            sys.exit(0)
        else:
            print("Available commands: list, add, bid, quit")
