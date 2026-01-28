import socket
import struct
import threading
import json
import random
import time
from datetime import datetime

# ------------------------------
# Configuration
# ------------------------------
MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5000
SERVER_PORT = random.randint(5001, 5010)
SERVER_ID = random.randint(1000, 9999)

STATE = {"items": []}
LEADER = None
KNOWN_SERVERS = {}  # port -> server_id
LAST_HEARTBEAT = {}  # port -> timestamp
CONNECTED_CLIENTS = set()
AUTHENTICATED_CLIENTS = {}  # client_port -> username

# Hard-coded credentials
USERS = {
    "alice": "password1",
    "bob": "password2",
    "charlie": "1234"
}

# Timing
STATUS_INTERVAL = 10
DISCOVERY_INTERVAL = 1
HEARTBEAT_INTERVAL = 2
HEARTBEAT_TIMEOUT = 6
FULL_STATE_INTERVAL = 5

# ------------------------------
# UDP sockets
# ------------------------------
server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_sock.bind(("0.0.0.0", SERVER_PORT))

mcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
mcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
mcast_sock.bind(("", MCAST_PORT))
mreq = struct.pack("4sl", socket.inet_aton(MCAST_GRP), socket.INADDR_ANY)
mcast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

print(f"[{SERVER_PORT}] Server started with ID {SERVER_ID}")

# ------------------------------
# Utilities
# ------------------------------
def send_to_multicast(msg):
    mcast_sock.sendto(msg.encode(), (MCAST_GRP, MCAST_PORT))

def send_to_server(port, msg):
    server_sock.sendto(msg.encode(), ("127.0.0.1", port))

def send_hello():
    msg = {"type": "HELLO", "server_port": SERVER_PORT, "server_id": SERVER_ID}
    send_to_multicast(json.dumps(msg))

def send_full_state(to_port):
    msg = {
        "type": "FULL_STATE",
        "leader": LEADER,
        "servers": KNOWN_SERVERS,
        "state": STATE,
        'authenticated_clients': AUTHENTICATED_CLIENTS  # PERP
    }
    send_to_server(to_port, json.dumps(msg))

def broadcast_state():
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            send_to_server(port, json.dumps({"type":"STATE_UPDATE","state":STATE, "authenticated_clients": AUTHENTICATED_CLIENTS}))

# ------------------------------
# HS leader election
# ------------------------------
def hs_election():
    global LEADER
    all_ids = list(KNOWN_SERVERS.values()) + [SERVER_ID]
    new_leader = max(all_ids)
    if LEADER != new_leader:
        LEADER = new_leader
        print(f"[{SERVER_PORT}] Leader elected: {LEADER}")

# ------------------------------
# Multicast listener
# ------------------------------
def multicast_listener():
    global KNOWN_SERVERS, LAST_HEARTBEAT
    while True:
        try:
            data, _ = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") != "HELLO":
            continue

        port = int(msg["server_port"])
        sid = msg["server_id"]
        if port == SERVER_PORT:
            continue

        is_new = port not in KNOWN_SERVERS
        KNOWN_SERVERS[port] = sid
        LAST_HEARTBEAT[port] = time.time()

        if is_new:
            print(f"[{SERVER_PORT}] Discovered server {sid} on port {port}")
            if LEADER == SERVER_ID:
                send_full_state(port)

# ------------------------------
# Periodic HELLO
# ------------------------------
def periodic_hello():
    while True:
        send_hello()
        time.sleep(DISCOVERY_INTERVAL)

# ------------------------------
# Full state broadcast (leader)
# ------------------------------
def full_state_broadcast():
    while True:
        if LEADER == SERVER_ID:
            for port in KNOWN_SERVERS:
                if port != SERVER_PORT:
                    send_full_state(port)
        time.sleep(FULL_STATE_INTERVAL)

# ------------------------------
# Leader monitor
# ------------------------------
def leader_monitor():
    global LEADER
    while True:
        if LEADER != SERVER_ID and LEADER is not None:
            leader_port = None
            for port, sid in KNOWN_SERVERS.items():
                if sid == LEADER:
                    leader_port = port
                    break
            now = time.time()
            if leader_port is None or (leader_port in LAST_HEARTBEAT and now - LAST_HEARTBEAT[leader_port] > HEARTBEAT_TIMEOUT):
                if leader_port:
                    KNOWN_SERVERS.pop(leader_port, None)
                    LAST_HEARTBEAT.pop(leader_port, None)
                hs_election()
        time.sleep(HEARTBEAT_INTERVAL)

# ------------------------------
# Leader monitor all servers
# ------------------------------
def leader_check_servers():
    while True:
        if LEADER == SERVER_ID:
            now = time.time()
            dead_ports = []
            for port in list(KNOWN_SERVERS.keys()):
                if port == SERVER_PORT:
                    continue
                if port not in LAST_HEARTBEAT or now - LAST_HEARTBEAT[port] > HEARTBEAT_TIMEOUT:
                    dead_ports.append(port)
            for port in dead_ports:
                print(f"[{SERVER_PORT}] Removing dead server {port}")
                KNOWN_SERVERS.pop(port)
                LAST_HEARTBEAT.pop(port, None)
        time.sleep(HEARTBEAT_INTERVAL)

# ------------------------------
# Auction timer
# ------------------------------
def auction_timer():
    while True:
        if LEADER == SERVER_ID:
            now = time.time()
            for item in STATE["items"]:
                if item["end_time"] and now > item["end_time"]:
                    item["end_time"] = now  # close auction
        time.sleep(1)

# ------------------------------
# Server listener
# ------------------------------
def server_listener():
    global STATE, KNOWN_SERVERS, LEADER, LAST_HEARTBEAT
    while True:
        data, addr = server_sock.recvfrom(4096)
        try:
            msg = json.loads(data.decode())
        except:
            continue

        if msg.get("type") == "STATE_UPDATE":
            STATE = msg.get("state", STATE)
            #STATE.update(msg.get('state', {}))
            AUTHENTICATED_CLIENTS.clear()  # PERP
            authenticated_client_list = msg.get('authenticated_clients')
            AUTHENTICATED_CLIENTS.update({int(k): v for k,v in authenticated_client_list.items()}) 
        elif msg.get("type") == "CLIENT":
            client_port = msg.get("client_port")
            CONNECTED_CLIENTS.add(client_port)
            command = msg.get("command")
            if SERVER_ID == LEADER:
                response = process_command(command, client_port)
                send_to_server(client_port, json.dumps({"type":"RESPONSE","data":response}))
                broadcast_state()
            else:
                leader_port = None
                for port, sid in KNOWN_SERVERS.items():
                    if sid == LEADER:
                        leader_port = port
                        break
                if leader_port:
                    send_to_server(leader_port, json.dumps(msg))

        elif msg.get("type") == "FULL_STATE":
            LEADER = msg.get("leader")
            KNOWN_SERVERS.update({int(port): sid for port, sid in msg.get("servers", {}).items()})
            STATE = msg.get("state", STATE)
            AUTHENTICATED_CLIENTS.clear()  # PERP
            authenticated_client_list = msg.get('authenticated_clients')
            AUTHENTICATED_CLIENTS.update({int(k): v for k,v in authenticated_client_list.items()}) 
            now = time.time()
            for port in KNOWN_SERVERS:
                LAST_HEARTBEAT[port] = now
            LAST_HEARTBEAT[SERVER_PORT] = now

        elif msg.get("type") == "PING":
            if SERVER_ID == LEADER:
                send_to_server(addr[1], json.dumps({"type":"PONG"}))

        elif msg.get("type") == "PONG":
            for port, sid in KNOWN_SERVERS.items():
                if port == addr[1]:
                    LAST_HEARTBEAT[port] = time.time()

# ------------------------------
# Command processor
# ------------------------------
def process_command(command, client_port):
    if command["action"] == "login":
        username = command.get("username")
        password = command.get("password")
        if USERS.get(username) == password:
            AUTHENTICATED_CLIENTS[client_port] = username
            broadcast_state()  # Syncs new auth to replicas
            return {"status":"success","message":f"Logged in as {username}"}
        else:
            return {"status":"error","message":"Invalid credentials"}
    if client_port not in AUTHENTICATED_CLIENTS:
        return {"status":"error","message":"Not authenticated"}
    print(f'{AUTHENTICATED_CLIENTS}') #for debugging remove
    username = AUTHENTICATED_CLIENTS[client_port]
    now = time.time()

    if command["action"] == "list":
        return [item for item in STATE["items"] if item["end_time"] > now]

    elif command["action"] == "add":
        item_id = len(STATE["items"]) + 1
        duration = command.get("duration", 60)
        start_time = now
        new_item = {
            "id": item_id,
            "name": command["name"],
            "description": command.get("description",""),
            "owner": username,
            "start_time": start_time,
            "end_time": start_time + duration,
            "start_price": command.get("start_price",0),
            "current_bid": 0,
            "last_bidder": None
        }
        STATE["items"].append(new_item)
        return {"status":"added","item_id":item_id}

    elif command["action"] == "bid":
        for item in STATE["items"]:
            if item["id"] == command["item_id"]:
                if item["end_time"] <= now:
                    return {"status":"error","message":"Auction closed"}
                if command["bid"] > item["current_bid"]:
                    item["current_bid"] = command["bid"]
                    item["last_bidder"] = username
                    return {"status":"bid accepted"}
                else:
                    return {"status":"bid too low"}
        return {"status":"error","message":"Item not found"}

    return {"status":"unknown command"}

# ------------------------------
# Status printer
# ------------------------------
def print_status():
    while True:
        sorted_ids = sorted(set(list(KNOWN_SERVERS.values()) + [SERVER_ID]))
        print(f"\n--- STATUS UPDATE ---")
        print(f"My ID: {SERVER_ID}")
        print(f"Known Server IDs: {sorted_ids}")
        print(f"Current Leader: {LEADER}")
        print(f"Auction Items: {len(STATE['items'])}")
        now = time.time()
        for item in STATE["items"]:
            start_time = datetime.fromtimestamp(item.get("start_time", now)).strftime('%Y-%m-%d %H:%M:%S')
            end_time = datetime.fromtimestamp(item.get("end_time", now)).strftime('%Y-%m-%d %H:%M:%S')
            print(f" - {item['name']} | Current Bid: {item['current_bid']} | Last Bidder: {item['last_bidder']} | "
                  f"Starts: {start_time} | Ends: {end_time}")
        print(f"Connected Clients: {list(CONNECTED_CLIENTS)}")
        print(f"Authenticated Clients: {list(AUTHENTICATED_CLIENTS)}")
        print("--------------------\n")
        time.sleep(STATUS_INTERVAL)

# ------------------------------
# Main
# ------------------------------
if __name__ == "__main__":
    KNOWN_SERVERS[SERVER_PORT] = SERVER_ID
    LAST_HEARTBEAT[SERVER_PORT] = time.time()

    threading.Thread(target=multicast_listener, daemon=True).start()
    threading.Thread(target=server_listener, daemon=True).start()
    threading.Thread(target=print_status, daemon=True).start()
    threading.Thread(target=periodic_hello, daemon=True).start()
    threading.Thread(target=leader_monitor, daemon=True).start()
    threading.Thread(target=leader_check_servers, daemon=True).start()
    threading.Thread(target=full_state_broadcast, daemon=True).start()
    threading.Thread(target=auction_timer, daemon=True).start()

    # Startup discovery window
    time.sleep(5)
    if LEADER is None:
        if len(KNOWN_SERVERS) == 1:
            LEADER = SERVER_ID
            print(f"[{SERVER_PORT}] No other servers found. Electing self as leader.")
        else:
            time.sleep(2)
            if LEADER is None:
                hs_election()

    while True:
        time.sleep(1)
