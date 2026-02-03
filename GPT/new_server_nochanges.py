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
KNOWN_SERVERS = {}      # port -> server_id
LAST_HEARTBEAT = {}     # port -> timestamp
CONNECTED_CLIENTS = set()
SESSIONS = {}           # session_id -> username

# NEW: map port -> ip (servers and clients)
PEER_IPS = {}           # port -> ip string

USERS = {
    "alice": "password1",
    "bob": "password2",
    "charlie": "1234"
}

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
    """
    Send to a peer (server or client) by port, using the last known IP.
    Falls back to 127.0.0.1 if we don't know the IP (e.g. same-machine).
    """
    ip = PEER_IPS.get(port, "127.0.0.1")
    server_sock.sendto(msg.encode(), (ip, port))

def send_hello():
    msg = {"type": "HELLO", "server_port": SERVER_PORT, "server_id": SERVER_ID}
    send_to_multicast(json.dumps(msg))

def send_full_state(to_port):
    msg = {
        "type": "FULL_STATE",
        "leader": LEADER,
        "servers": KNOWN_SERVERS,
        "state": STATE,
        "sessions": SESSIONS,
        "users": USERS
    }
    send_to_server(to_port, json.dumps(msg))

def broadcast_state():
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            send_to_server(port, json.dumps({
                "type": "STATE_UPDATE",
                "state": STATE,
                "sessions": SESSIONS,
                "users": USERS
            }))

def normalize_sessions(sessions_dict):
    normalized = {}
    for k, v in sessions_dict.items():
        try:
            sid = int(k)
        except:
            sid = k
        normalized[sid] = v
    return normalized

# ------------------------------
# Session replication helpers
# ------------------------------
def send_session_update():
    if LEADER != SERVER_ID:
        return
    msg = {"type": "SESSION_UPDATE", "sessions": SESSIONS}
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            send_to_server(port, json.dumps(msg))

def request_sessions_from_others():
    if LEADER != SERVER_ID:
        return
    msg = {"type": "SESSION_REQUEST"}
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            send_to_server(port, json.dumps(msg))

def merge_sessions(remote_sessions):
    global SESSIONS
    remote_sessions = normalize_sessions(remote_sessions)
    for sid, username in remote_sessions.items():
        if sid not in SESSIONS:
            SESSIONS[sid] = username

# ------------------------------
# Leader Election
# ------------------------------
def hs_election():
    global LEADER
    all_ids = list(KNOWN_SERVERS.values()) + [SERVER_ID]
    new_leader = max(all_ids)
    if LEADER != new_leader:
        LEADER = new_leader
        print(f"[{SERVER_PORT}] Leader elected: {LEADER}")
        if LEADER == SERVER_ID:
            request_sessions_from_others()

# ------------------------------
# Multicast Listener
# ------------------------------
def multicast_listener():
    global KNOWN_SERVERS, LAST_HEARTBEAT, PEER_IPS
    while True:
        try:
            data, addr = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue
        if msg.get("type") != "HELLO":
            continue

        port = int(msg["server_port"])
        sid = msg["server_id"]
        ip = addr[0]

        if port == SERVER_PORT:
            continue

        is_new = port not in KNOWN_SERVERS
        KNOWN_SERVERS[port] = sid
        LAST_HEARTBEAT[port] = time.time()
        PEER_IPS[port] = ip

        if is_new and LEADER == SERVER_ID:
            send_full_state(port)

# ------------------------------
# Periodic HELLO
# ------------------------------
def periodic_hello():
    while True:
        send_hello()
        time.sleep(DISCOVERY_INTERVAL)

# ------------------------------
# Full State Broadcast
# ------------------------------
def full_state_broadcast():
    while True:
        if LEADER == SERVER_ID:
            broadcast_state()
        time.sleep(FULL_STATE_INTERVAL)

# ------------------------------
# Leader Monitor
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
            if leader_port is None or now - LAST_HEARTBEAT.get(leader_port, 0) > HEARTBEAT_TIMEOUT:
                if leader_port:
                    KNOWN_SERVERS.pop(leader_port, None)
                    LAST_HEARTBEAT.pop(leader_port, None)
                hs_election()
        time.sleep(HEARTBEAT_INTERVAL)

def leader_check_servers():
    while True:
        if LEADER == SERVER_ID:
            now = time.time()
            dead = []
            for port in list(KNOWN_SERVERS.keys()):
                if port == SERVER_PORT:
                    continue
                if now - LAST_HEARTBEAT.get(port, 0) > HEARTBEAT_TIMEOUT:
                    dead.append(port)
            for port in dead:
                print(f"[{SERVER_PORT}] Removing dead server {port}")
                KNOWN_SERVERS.pop(port, None)
                LAST_HEARTBEAT.pop(port, None)
                PEER_IPS.pop(port, None)
        time.sleep(HEARTBEAT_INTERVAL)

# ------------------------------
# Auction Timer
# ------------------------------
def auction_timer():
    while True:
        if LEADER == SERVER_ID:
            now = time.time()
            for item in STATE["items"]:
                if item["end_time"] and now > item["end_time"]:
                    item["end_time"] = now
        time.sleep(1)

# ------------------------------
# Process Command (Leader Only)
# ------------------------------
def process_command(command):
    global STATE, SESSIONS, USERS

    # REGISTER
    if command["action"] == "register":
        username = command.get("username")
        password = command.get("password")

        if username in USERS:
            return {"status": "error", "message": "User already exists"}

        USERS[username] = password

        session_id = random.randint(10000, 99999)
        SESSIONS[session_id] = username

        broadcast_state()
        send_session_update()

        return {
            "status": "success",
            "message": f"User '{username}' created and logged in",
            "session_id": session_id
        }

    # LOGIN
    if command["action"] == "login":
        username = command.get("username")
        password = command.get("password")

        if username not in USERS:
            return {
                "status": "new_user",
                "message": f"User '{username}' does not exist. Create new account?"
            }

        if USERS[username] != password:
            return {"status": "error", "message": "Invalid password"}

        session_id = random.randint(10000, 99999)
        SESSIONS[session_id] = username

        broadcast_state()
        send_session_update()

        return {"status": "success", "message": f"Logged in as {username}", "session_id": session_id}

    # SESSION VALIDATION
    session_id = command.get("session_id")
    if session_id not in SESSIONS:
        return {"status": "error", "message": "Not authenticated"}

    username = SESSIONS[session_id]
    now = time.time()

    # LIST
    if command["action"] == "list":
        return [item for item in STATE["items"] if item["end_time"] > now]

    # ADD
    if command["action"] == "add":
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
        broadcast_state()
        return {"status":"added","item_id":item_id}

    # BID
    if command["action"] == "bid":
        for item in STATE["items"]:
            if item["id"] == command["item_id"]:
                if item["end_time"] <= now:
                    return {"status":"error","message":"Auction closed"}

                min_bid = max(item["start_price"], item["current_bid"])

                if command["bid"] > min_bid:
                    item["current_bid"] = command["bid"]
                    item["last_bidder"] = username
                    broadcast_state()
                    return {"status":"bid accepted"}
                else:
                    return {
                        "status": "bid too low",
                        "message": f"Minimum bid is {min_bid}"
                    }

        return {"status":"error","message":"Item not found"}

    return {"status":"unknown command"}

# ------------------------------
# Server Listener
# ------------------------------
def server_listener():
    global STATE, KNOWN_SERVERS, LEADER, LAST_HEARTBEAT, SESSIONS, USERS, PEER_IPS
    while True:
        data, addr = server_sock.recvfrom(4096)
        try:
            msg = json.loads(data.decode())
        except:
            continue

        mtype = msg.get("type")

        if mtype == "CLIENT":
            client_port = msg.get("client_port")
            # remember client IP
            if client_port is not None:
                PEER_IPS[client_port] = addr[0]

            command = msg.get("command")

            if SERVER_ID == LEADER:
                response = process_command(command)
                send_to_server(client_port, json.dumps({"type":"RESPONSE","data":response}))
            else:
                leader_port = None
                for port, sid in KNOWN_SERVERS.items():
                    if sid == LEADER:
                        leader_port = port
                        break

                if leader_port:
                    send_to_server(client_port, json.dumps({
                        "type": "REDIRECT",
                        "leader_port": leader_port
                    }))
                else:
                    send_to_server(client_port, json.dumps({
                        "type": "RESPONSE",
                        "data": {"status": "error", "message": "No leader available"}
                    }))

        elif mtype == "STATE_UPDATE":
            STATE.update(msg.get("state", {}))
            SESSIONS.update(normalize_sessions(msg.get("sessions", {})))
            USERS.update(msg.get("users", {}))

        elif mtype == "FULL_STATE":
            LEADER = msg.get("leader")
            KNOWN_SERVERS.update({int(p): s for p,s in msg.get("servers", {}).items()})
            STATE.update(msg.get("state", {}))
            SESSIONS.update(normalize_sessions(msg.get("sessions", {})))
            USERS.update(msg.get("users", {}))
            now = time.time()
            for port in KNOWN_SERVERS:
                LAST_HEARTBEAT[port] = now
            LAST_HEARTBEAT[SERVER_PORT] = now

        elif mtype == "SESSION_UPDATE":
            SESSIONS.update(normalize_sessions(msg.get("sessions", {})))
            ack = {"type": "SESSION_ACK", "server_port": SERVER_PORT}
            server_sock.sendto(json.dumps(ack).encode(), addr)

        elif mtype == "SESSION_REQUEST":
            if LEADER != SERVER_ID:
                dump = {
                    "type": "SESSION_DUMP",
                    "sessions": SESSIONS,
                    "server_port": SERVER_PORT
                }
                server_sock.sendto(json.dumps(dump).encode(), addr)

        elif mtype == "SESSION_DUMP":
            if LEADER == SERVER_ID:
                merge_sessions(msg.get("sessions", {}))

        elif mtype == "SESSION_ACK":
            pass

# ------------------------------
# Status Printer
# ------------------------------
def print_status():
    while True:
        sorted_ids = sorted(set(list(KNOWN_SERVERS.values()) + [SERVER_ID]))
        print(f"\n--- STATUS ---\nID: {SERVER_ID} | Leader: {LEADER} | Known: {sorted_ids}")
        print(f"Auction items: {len(STATE['items'])}")
        print(f"Sessions: {SESSIONS}")
        print(f"Users: {USERS}\n----------------------")
        time.sleep(STATUS_INTERVAL)

# ------------------------------
# Main
# ------------------------------
if __name__ == "__main__":
    KNOWN_SERVERS[SERVER_PORT] = SERVER_ID
    LAST_HEARTBEAT[SERVER_PORT] = time.time()
    # local server IP for self (not strictly needed, but consistent)
    PEER_IPS[SERVER_PORT] = "127.0.0.1"

    threading.Thread(target=multicast_listener, daemon=True).start()
    threading.Thread(target=server_listener, daemon=True).start()
    threading.Thread(target=print_status, daemon=True).start()
    threading.Thread(target=periodic_hello, daemon=True).start()
    threading.Thread(target=leader_monitor, daemon=True).start()
    threading.Thread(target=leader_check_servers, daemon=True).start()
    threading.Thread(target=full_state_broadcast, daemon=True).start()
    threading.Thread(target=auction_timer, daemon=True).start()

    time.sleep(5)
    if LEADER is None:
        if len(KNOWN_SERVERS) == 1:
            LEADER = SERVER_ID
            print(f"[{SERVER_PORT}] Electing self as leader.")
            request_sessions_from_others()
        else:
            time.sleep(2)
            if LEADER is None:
                hs_election()

    while True:
        time.sleep(1)
