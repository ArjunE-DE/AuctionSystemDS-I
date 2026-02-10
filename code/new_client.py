import socket
import struct
import json
import random
import threading
import time
import sys

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

    u = input("Username: ")
    p = input("Password: ")

    resp = send_command({
        "action": "login",
        "username": u,
        "password": p
    })

    if resp and resp["type"] == "RESPONSE":
        data = resp["data"]
        if data.get("status") == "success":
            SESSION_ID = data["session_id"]
            print("Logged in.")


def register():
    global SESSION_ID

    u = input("Choose username: ")
    p = input("Choose password: ")

    resp = send_command({
        "action": "register",
        "username": u,
        "password": p
    })

    if resp and resp["type"] == "RESPONSE":
        data = resp["data"]
        if data.get("status") == "success":
            SESSION_ID = data["session_id"]
            print("Registered.")


def list_auctions():
    resp = send_command({
        "action": "list",
        "session_id": SESSION_ID
    })

    if resp and resp["type"] == "RESPONSE":
        print(resp["data"])


def add():
    name = input("Name: ")
    desc = input("Description: ")
    dur = int(input("Duration: "))
    price = float(input("Start price: "))

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


def bid():
    i = int(input("Item id: "))
    a = float(input("Amount: "))

    resp = send_command({
        "action": "bid",
        "item_id": i,
        "bid": a,
        "session_id": SESSION_ID
    })

    if resp:
        print(resp)


# ---------------- MAIN ----------------

print("[CLIENT] Waiting for servers...")
while not KNOWN_SERVERS:
    time.sleep(0.5)

print("[CLIENT] Servers:", KNOWN_SERVERS)

while True:

    cmd = input("\nlogin | register | list | add | bid | quit > ")

    if cmd == "login":
        login()

    elif cmd == "register":
        register()

    elif cmd == "list":
        list_auctions()

    elif cmd == "add":
        add()

    elif cmd == "bid":
        bid()

    elif cmd == "quit":
        sys.exit(0)
