import socket
import struct
import threading
import json
import random
import time
from datetime import datetime
from math import ceil, log2

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
SESSIONS = {}           # session_id -> username
ACK_TRACKER = {}      # port -> expected ACK time

# NEW: map port -> ip (servers and clients)
PEER_IPS = {}           # port -> ip string

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

# Max HS initiations per node
MAX_HS_INIT = 3
HS_INIT_COUNT = 0

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

# -----------------------------
# Lamport Clock
# -----------------------------
class LamportClock:
    def __init__(self, start: int = 0):
        self.time = int(start)
        self._lock = threading.Lock()

    def tick(self) -> int:
        """Increment the clock for an outgoing event."""
        with self._lock:
            self.time += 1
            return self.time

    def update(self, received_ts: int) -> int:
        """Update the clock based on a received timestamp."""
        with self._lock:
            self.time = max(self.time, int(received_ts)) + 1
            return self.time

    def read(self) -> int:
        """Read the current clock value."""
        with self._lock:
            return int(self.time)

# Initialize the Lamport clock
LAMPORT = LamportClock()
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
    if port is None:
        return
    ip = PEER_IPS.get(port, "127.0.0.1")
    msg = json.loads(msg)  # Ensure the message is a dictionary
    server_sock.sendto(json.dumps(msg).encode(), (ip, port))

def send_ack_to_leader():
    """Send an acknowledgement message to the leader server."""
    if LEADER == SERVER_ID:
        return  # No need to ack if we are the leader
    if LEADER is None:
        print(f"[{SERVER_PORT}] No leader to acknowledge.")
        return

    # Find the leader's port
    leader_port = None
    for port, sid in KNOWN_SERVERS.items():
        if sid == LEADER:
            leader_port = port
            break

    if leader_port is None:
        print(f"[{SERVER_PORT}] Leader not found in known servers.")
        return

    # Construct and send the acknowledgement message
    ack_msg = {
        "type": "ACK",
        "server_port": SERVER_PORT,
        "server_id": SERVER_ID,
        "ts": LAMPORT.read()  # Attach Lamport timestamp
    }
    send_to_server(leader_port, json.dumps(ack_msg))
    print(f"[{SERVER_PORT}] Sent ACK to leader {LEADER} at port {leader_port}.")

def send_hello():
    msg = {"type": "HELLO", "server_port": SERVER_PORT, "server_id": SERVER_ID}
    send_to_multicast(json.dumps(msg))

def send_full_state(to_port=None):
    msg = {
        "type": "FULL_STATE",
        "leader": LEADER,
        "servers": KNOWN_SERVERS,
        "state": STATE,
        "sessions": SESSIONS,
        "users": USERS
    }
    #print(f'This is the leader in send full state: {LEADER}')
    send_to_server(to_port, json.dumps(msg))

def broadcast_state(action={}):
    if 'add' in action or 'bid' in action:
        LAMPORT.tick()
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            ACK_TRACKER[port] = time.time() + 5  # Expect ACK within 5 seconds
            send_to_server(port, json.dumps({
                "type": "STATE_UPDATE",
                "state": STATE,
                "sessions": SESSIONS,
                "users": USERS,
                "action": action,
                "ts": LAMPORT.read()
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
# Hirschberg-Sinclair state
# ------------------------------
HS_STATE = {
    "current_phase": 0,
    "replies_received": {"LEFT": False, "RIGHT": False},
    "phase_in_progress": False,
    "max_phase": 0,
    "candidate_id": None
}

def get_ring_neighbors():
    ports = sorted(KNOWN_SERVERS.keys())
    idx = ports.index(SERVER_PORT)
    left = ports[idx - 1] if idx > 0 else ports[-1]
    right = ports[(idx + 1) % len(ports)]
    return left, right

def send_hs_election(candidate_id, phase, direction, hop, max_hop):
    left, right = get_ring_neighbors()
    target = left if direction == "LEFT" else right
    print(f"[{SERVER_PORT}] Sending HS_ELECTION: candidate={candidate_id}, phase={phase}, "
          f"direction={direction}, hop={hop}/{max_hop} -> port {target}")
    msg = {
        "type": "HS_ELECTION",
        "candidate_id": candidate_id,
        "phase": phase,
        "direction": direction,
        "hop": hop,
        "max_hop": max_hop,
        "origin": SERVER_PORT
    }
    send_to_server(target, json.dumps(msg))

def send_hs_reply(candidate_id, direction, origin):
    left, right = get_ring_neighbors()
    target = left if direction == "LEFT" else right
    print(f"[{SERVER_PORT}] Sending HS_REPLY: candidate={candidate_id}, direction={direction} -> port {target}")
    msg = {
        "type": "HS_REPLY",
        "candidate_id": candidate_id,
        "direction": direction,
        "origin": origin
    }
    send_to_server(target, json.dumps(msg))

def hs_election():
    global HS_INIT_COUNT
    if HS_INIT_COUNT >= MAX_HS_INIT or len(KNOWN_SERVERS) == 0:
        return    
    HS_INIT_COUNT += 1
    HS_STATE["candidate_id"] = SERVER_ID
    HS_STATE["current_phase"] = 0
    HS_STATE["replies_received"] = {"LEFT": False, "RIGHT": False}
    HS_STATE["phase_in_progress"] = True
    HS_STATE["max_phase"] = ceil(log2(len(KNOWN_SERVERS) / 2)) if len(KNOWN_SERVERS) > 1 else 0

    # Singleton node case
    if len(KNOWN_SERVERS) == 1:
        global LEADER
        LEADER = SERVER_ID
        print(f"[{SERVER_PORT}] Only node alive, self elected as leader: {LEADER}")
        return

    ring_sorted = sorted(KNOWN_SERVERS.values())
    print(f"[{SERVER_PORT}] Starting HS election as candidate {SERVER_ID}")
    print(f"[{SERVER_PORT}] Ring for election: {ring_sorted}")
    send_hs_election(SERVER_ID, 0, "LEFT", 1, 1)
    send_hs_election(SERVER_ID, 0, "RIGHT", 1, 1)

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
# Full State Broadcast
# ------------------------------
def full_state_broadcast():
    while True:
        if LEADER == SERVER_ID:
            broadcast_state({"Full state update from leader": SERVER_ID})
            for port in KNOWN_SERVERS:
                if port != SERVER_PORT:
                    send_full_state(port)
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
            if leader_port is None or (leader_port in LAST_HEARTBEAT and now - LAST_HEARTBEAT[leader_port] > HEARTBEAT_TIMEOUT):
                if leader_port:
                    # Remove dead leader first
                    print(f"[{SERVER_PORT}] Removing dead leader {LEADER}")
                    KNOWN_SERVERS.pop(leader_port, None)
                    LAST_HEARTBEAT.pop(leader_port, None)
                # Broadcast updated server list before HS
                #send_full_state()
                broadcast_state()
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
                if port not in LAST_HEARTBEAT or now - LAST_HEARTBEAT[port] > HEARTBEAT_TIMEOUT:
                    dead.append(port)
            for port in dead:
                print(f"[{SERVER_PORT}] Removing dead server {KNOWN_SERVERS[port]}")
                KNOWN_SERVERS.pop(port, None)
                LAST_HEARTBEAT.pop(port, None)
                PEER_IPS.pop(port, None)
                        # After removing dead servers, broadcast full state
            if dead:
                #send_full_state()
                broadcast_state()
        time.sleep(HEARTBEAT_INTERVAL)

# ------------------------------
# Auction Monitor
# ------------------------------
def auction_monitor():
    while True:
        current_time = time.time()
        for item in STATE["items"]:
            end_time = item.get('end_time')
            if end_time and current_time > end_time:
                last_bidder = item.get('last_bidder')
                item_name = item.get('name')
                winner_session_id = list(SESSIONS.keys())[list(SESSIONS.values()).index(last_bidder)] if last_bidder in SESSIONS.values() else None
                print(f"Auction for {item_name} has ended. Last bidder: {last_bidder}, session id: {winner_session_id}")
                for i in range(6000, 7000):
                    if PEER_IPS.get(i):
                        send_to_server(i, json.dumps({
                            "type": "RESPONSE",
                            "data": {
                                "winner_session_id": winner_session_id,
                                "status": "auction won",
                                "message": f"{last_bidder} has won the auction for item: {item_name}, with a bid of {item.get('current_bid')}"
                            }
                        }))
                STATE["items"].remove(item)
        time.sleep(5)  # Check every 5 seconds

                # msg = {
                #     "type": "RESPONSE", 
                #     "server_port": SERVER_PORT, 
                #     "server_id": SERVER_ID
                #     }
                # send_to_multicast(json.dumps(msg))
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

        broadcast_state({"New user created": username})
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

        broadcast_state({"User login": username})
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
            "current_bid": command.get("start_price",0),
            "last_bidder": None
        }
        STATE["items"].append(new_item)
        broadcast_state({"add": new_item})
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
                    broadcast_state({"bid": item["id"], "bid amount": command["bid"], "bidder": username})
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
        # Update Lamport clock with the received timestamp
        #send_ack_to_leader()

        if "action" in msg and "ts" in msg and SERVER_ID is not LEADER:
            print("Local LAMPORT Time:", LAMPORT.read(), "Received TS:", msg["ts"])
            print(f"[{SERVER_PORT}] Received message: {msg['action']} from {addr} at timestamp {LAMPORT.read()}")
            if LAMPORT.read() == msg["ts"] - 1:
                LAMPORT.tick()
            elif LAMPORT.read() < msg["ts"]:
                LAMPORT.update(msg["ts"])
        mtype = msg.get("type")
        # ---------------- HS messages ----------------
        if msg.get("type") == "HS_ELECTION":
            candidate = msg["candidate_id"]
            phase = msg["phase"]
            direction = msg["direction"]
            hop = msg["hop"]
            max_hop = msg["max_hop"]
            origin = msg["origin"]
            print(f"[{SERVER_PORT}] Received HS_ELECTION: candidate={candidate}, phase={phase}, "
                  f"direction={direction}, hop={hop}/{max_hop} from {addr[1]} origin={origin}")

            if candidate == SERVER_ID:
                # Ignore own candidate message
                pass
            elif candidate > SERVER_ID:
                if hop < max_hop:
                    # Forward to next node
                    send_hs_election(candidate, phase, direction, hop + 1, max_hop)
                else:
                    # Reply back
                    reply_dir = "LEFT" if direction == "RIGHT" else "RIGHT"
                    send_hs_reply(candidate, reply_dir, origin)
            else:
                print(f"[{SERVER_PORT}] Ignoring HS_ELECTION from lower candidate {candidate}")

        elif msg.get("type") == "HS_REPLY":
            candidate = msg["candidate_id"]
            direction = msg["direction"]
            origin = msg["origin"]
            print(f"[{SERVER_PORT}] Received HS_REPLY: candidate={candidate}, direction={direction} from {addr[1]}")
            if candidate != SERVER_ID:
                send_hs_reply(candidate, direction, origin)
            else:
                HS_STATE["replies_received"][direction] = True
                if all(HS_STATE["replies_received"].values()):
                    if HS_STATE["current_phase"] >= HS_STATE["max_phase"]:
                        LEADER = SERVER_ID
                        HS_STATE["phase_in_progress"] = False
                        print(f"[{SERVER_PORT}] Leader elected via HS: {LEADER}")
                        announce = { 
                            "type": "FULL_STATE", "leader": LEADER, "servers": KNOWN_SERVERS, "state": STATE, "sessions": SESSIONS, "users": USERS } 
                        for port in KNOWN_SERVERS:  
                            send_to_server(port, json.dumps(announce))
                    else:
                        # Increment phase
                        HS_STATE["current_phase"] += 1
                        phase = HS_STATE["current_phase"]
                        HS_STATE["replies_received"] = {"LEFT": False, "RIGHT": False}
                        print(f"[{SERVER_PORT}] Incrementing HS phase to {phase}, restarting election")
                        max_hop = 2 ** phase
                        send_hs_election(SERVER_ID, phase, "LEFT", 1, max_hop)
                        send_hs_election(SERVER_ID, phase, "RIGHT", 1, max_hop)

        elif mtype == "CLIENT":
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


        elif msg.get("type") == "FULL_STATE":
            old_leader = LEADER
            LEADER = msg.get("leader")
            if LEADER != old_leader:
                print(f"[{SERVER_PORT}] Adopting leader info from node: {LEADER}")
            # Sync KNOWN_SERVERS: remove nodes not present in leader's list
            leader_servers = {int(port): sid for port, sid in msg.get("servers", {}).items()}
            for port in list(KNOWN_SERVERS.keys()):
                if port not in leader_servers:
                    print(f"[{SERVER_PORT}] Removing unknown server {port} per leader update")
                    KNOWN_SERVERS.pop(port, None)
                    LAST_HEARTBEAT.pop(port, None)
            KNOWN_SERVERS.update(leader_servers)
            STATE = msg.get("state", STATE)
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

        elif mtype == "ACK":
            if LEADER == SERVER_ID and time.time() < ACK_TRACKER.get(msg.get("server_port"), 0):
                print(f"[{SERVER_PORT}] Received ACK from server at port {msg.get('server_port')}")
                ACK_TRACKER.pop(msg.get("server_port"), None)
            else:
                print(f"[{SERVER_PORT}] Received late ACK from server at port {msg.get('server_port')}")
                send_to_server(msg.get('server_port'), json.dumps({
                    "type": "FULL_STATE",
                    "leader": LEADER,
                    "servers": KNOWN_SERVERS,
                    "state": STATE,
                    "sessions": SESSIONS,
                    "users": USERS,
                    "ts": LAMPORT.read()
                }))
            pass

# ------------------------------
# Status Printer
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
        print(f"Connected Clients: {SESSIONS}")
        print("--------------------\n")
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
    threading.Thread(target=auction_monitor, daemon=True).start()

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
    for port in ACK_TRACKER:
        if time.time() > ACK_TRACKER[port]:
            print(f"[{SERVER_PORT}] No ACK from server at port {port}, sending full state.")
            send_to_server(port, json.dumps({
                "type": "FULL_STATE",
                "leader": LEADER,
                "servers": KNOWN_SERVERS,
                "state": STATE,
                "sessions": SESSIONS,
                "users": USERS,
                "ts": LAMPORT.read()
            }))
    while True:
        time.sleep(1)
