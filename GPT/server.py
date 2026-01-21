import socket
import threading
import time
import json
import random
from datetime import datetime, timedelta

# --------------------------- CONFIG ---------------------------
MULTICAST_GROUP = '224.1.1.1'
MULTICAST_PORT = 5007
SERVER_PORT = random.randint(10000, 60000)
HEARTBEAT_INTERVAL = 5
HEARTBEAT_TIMEOUT = 10
DISCOVERY_WAIT = 3           # initial wait for discovery messages
SNAPSHOT_RETRY_INTERVAL = 2  # interval for requesting snapshots
SNAPSHOT_MAX_RETRIES = 5     # max attempts before declaring self leader

server_id = random.randint(1, 1000)
leader_id = None
hs_election_in_progress = False

servers = {}   # {server_id: (ip, port, last_heartbeat)}
clients = {}   # {(ip, port): last_seen}
auctions = {}  # {auction_id: auction_dict}

servers_lock = threading.Lock()
clients_lock = threading.Lock()
auctions_lock = threading.Lock()
hs_lock = threading.Lock()

snapshot_pending = set()
snapshot_lock = threading.Lock()
hs_state = {}

# --------------------------- SOCKETS ---------------------------
mcast_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
mcast_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
try:
    mcast_sock.bind(('', MULTICAST_PORT))
except:
    mcast_sock.bind((socket.gethostbyname(socket.gethostname()), MULTICAST_PORT))
mreq = socket.inet_aton(MULTICAST_GROUP) + socket.inet_aton('0.0.0.0')
mcast_sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

server_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
server_sock.bind(('', SERVER_PORT))

# --------------------------- HELPERS ---------------------------
def print_status():
    global leader_id
    with servers_lock:
        all_servers = dict(servers)
        all_servers[server_id] = ('self', SERVER_PORT, time.time())
    with clients_lock:
        client_list = list(clients.keys())
    with auctions_lock:
        auction_list = list(auctions.values())

    print("\n====== SERVER STATUS ======")
    print(f"Server ID: {server_id} | Leader: {leader_id}")
    print("Connected Servers:")
    for sid, (ip, port, _) in sorted(all_servers.items()):
        print(f"  {sid}: {ip if ip != 'self' else '127.0.0.1'}:{port}" + (" (self)" if ip=='self' else ""))
    print("Connected Clients:")
    for addr in client_list:
        print(f"  {addr[0]}:{addr[1]}")
    print("Auctions:")
    for a in auction_list:
        print(f"  {a['id']}: {a['name']} | Last Bid: {a['current_bid']} | Bidder: {a['current_bidder']} | Ends: {a['end_time']}")
    print("===========================\n")

def send_unicast(ip, port, msg):
    server_sock.sendto(msg.encode(), (ip, port))

def send_multicast(msg):
    mcast_sock.sendto(msg.encode(), (MULTICAST_GROUP, MULTICAST_PORT))

def broadcast_to_servers(msg):
    with servers_lock:
        for sid, (ip, port, _) in servers.items():
            send_unicast(ip, port, msg)

def broadcast_leader():
    global leader_id
    if leader_id is not None:
        msg = json.dumps({"type": "hs_leader", "leader_id": leader_id})
        broadcast_to_servers(msg)

# --------------------------- DISCOVERY ---------------------------
def discovery_listener():
    global leader_id
    while True:
        try:
            data, addr = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
            mtype = msg.get("type")
            if mtype == "discovery":
                sid = msg["server_id"]
                if sid == server_id:
                    continue
                new_server = False
                with servers_lock:
                    if sid not in servers:
                        servers[sid] = (addr[0], msg["port"], time.time())
                        new_server = True
                    else:
                        servers[sid] = (addr[0], msg["port"], time.time())
                print_status()

                # If this server is leader, mark new server pending snapshot
                if leader_id == server_id and new_server:
                    with snapshot_lock:
                        snapshot_pending.add(sid)

            elif mtype == "snapshot":
                # Received snapshot from leader
                with servers_lock:
                    for sid_, info in msg["servers"].items():
                        servers[int(sid_)] = (info["ip"], info["port"], time.time())
                leader_id = msg["leader_id"]
                print_status()
                # trigger HS after snapshot
                trigger_hs_election_safe()
        except:
            continue

def announce_presence():
    while True:
        msg = json.dumps({"type": "discovery", "server_id": server_id, "port": SERVER_PORT})
        send_multicast(msg)
        time.sleep(2)

# --------------------------- SNAPSHOT SENDER ---------------------------
def snapshot_sender():
    global leader_id
    while True:
        if leader_id == server_id:
            to_remove = []
            with snapshot_lock, servers_lock:
                for sid in list(snapshot_pending):
                    if sid not in servers:
                        to_remove.append(sid)
                        continue
                    ip, port, _ = servers[sid]
                    snapshot_msg = {
                        "type": "snapshot",
                        "servers": {str(sid_): {"ip": ip2, "port": port2} for sid_, (ip2, port2, _) in servers.items()},
                        "leader_id": leader_id,
                        "clients": list(clients.keys()),
                        "auctions": list(auctions.values())
                    }
                    send_unicast(ip, port, json.dumps(snapshot_msg))
                    to_remove.append(sid)
                for sid in to_remove:
                    snapshot_pending.discard(sid)
        time.sleep(SNAPSHOT_RETRY_INTERVAL)

# --------------------------- SNAPSHOT REQUEST ---------------------------
def snapshot_request_loop():
    global leader_id
    retries = 0
    while leader_id is None and retries < SNAPSHOT_MAX_RETRIES:
        msg = json.dumps({"type": "discovery", "server_id": server_id, "port": SERVER_PORT})
        send_multicast(msg)
        time.sleep(SNAPSHOT_RETRY_INTERVAL)
        retries += 1

    if leader_id is None:
        leader_id = server_id
        print(f"[INFO] No leader detected after retries → self-declare leader {server_id}")
        broadcast_leader()

# --------------------------- HEARTBEAT ---------------------------
def heartbeat_sender():
    global leader_id
    while True:
        if leader_id == server_id:
            msg = json.dumps({"type": "heartbeat", "leader_id": server_id})
            broadcast_to_servers(msg)
        time.sleep(HEARTBEAT_INTERVAL)

def heartbeat_checker():
    global leader_id
    while True:
        time.sleep(HEARTBEAT_INTERVAL)
        removed = False
        with servers_lock:
            for sid in list(servers.keys()):
                if sid == server_id:
                    continue
                _, _, last = servers[sid]
                if time.time() - last > HEARTBEAT_TIMEOUT:
                    print(f"[INFO] Server {sid} timed out, removing from list")
                    servers.pop(sid)
                    removed = True
                    if leader_id == sid:
                        print(f"[INFO] Leader {leader_id} removed. Triggering HS election.")
                        leader_id = None
                        trigger_hs_election_safe()
        if removed:
            print_status()

# --------------------------- HS ELECTION ---------------------------
def get_ring_neighbors(snapshot):
    sorted_ids = sorted(list(snapshot.keys()))
    idx = sorted_ids.index(server_id)
    left = sorted_ids[idx - 1] if idx > 0 else sorted_ids[-1]
    right = sorted_ids[(idx + 1) % len(sorted_ids)]
    return left, right

def trigger_hs_election(origin=None):
    global hs_election_in_progress, leader_id
    if leader_id is not None:
        return  # Don't run HS if leader exists

    with hs_lock:
        if hs_election_in_progress:
            return
        hs_election_in_progress = True

    snapshot = {}
    with servers_lock:
        snapshot = dict(servers)
        snapshot[server_id] = ('self', SERVER_PORT, time.time())

    origin_id = origin if origin is not None else server_id
    hs_state[origin_id] = {"responses": {"left": None, "right": None}, "max_candidate": server_id, "snapshot": snapshot}
    left, right = get_ring_neighbors(snapshot)
    send_candidate(left, origin_id, "left", server_id, snapshot)
    send_candidate(right, origin_id, "right", server_id, snapshot)
    print(f"[INFO] HS election started by {origin_id}")

def send_candidate(target_id, origin, direction, candidate, snapshot):
    if target_id == server_id:
        handle_hs_message({"candidate": candidate, "origin": origin, "direction": direction})
        return
    with servers_lock:
        if target_id not in servers:
            return
        ip, port, _ = servers[target_id]
    msg = json.dumps({
        "type": "hs_election",
        "candidate": candidate,
        "origin": origin,
        "direction": direction
    })
    send_unicast(ip, port, msg)

def handle_hs_message(msg):
    global leader_id, hs_election_in_progress
    candidate = msg["candidate"]
    origin = msg["origin"]
    direction = msg["direction"]

    if leader_id is not None:
        return  # Ignore HS if leader exists

    if origin not in hs_state:
        with servers_lock:
            snapshot = dict(servers)
            snapshot[server_id] = ('self', SERVER_PORT, time.time())
        hs_state[origin] = {"responses": {"left": None, "right": None}, "max_candidate": candidate, "snapshot": snapshot}

    hs_state[origin]["max_candidate"] = max(hs_state[origin]["max_candidate"], candidate)
    snapshot = hs_state[origin]["snapshot"]
    left, right = get_ring_neighbors(snapshot)
    next_target = left if direction=="left" else right

    if next_target != origin:
        send_candidate(next_target, origin, direction, hs_state[origin]["max_candidate"], snapshot)
    else:
        hs_state[origin]["responses"][direction] = hs_state[origin]["max_candidate"]
        if all(v is not None for v in hs_state[origin]["responses"].values()):
            leader_id = hs_state[origin]["max_candidate"]
            hs_election_in_progress = False
            print(f"[INFO] Hirschberg-Sinclair leader elected: {leader_id}")
            broadcast_leader()
            print_status()
            hs_state.pop(origin)

def trigger_hs_election_safe():
    threading.Thread(target=trigger_hs_election, daemon=True).start()

# --------------------------- SERVER HANDLER ---------------------------
def handle_server_message(msg, addr):
    global leader_id
    mtype = msg.get("type")
    if mtype == "heartbeat":
        sid = msg["leader_id"]
        if sid != server_id:
            with servers_lock:
                servers[sid] = (addr[0], addr[1], time.time())
    elif mtype == "hs_leader":
        leader_id = msg["leader_id"]
        print(f"[INFO] Leader announced: {leader_id}")
        print_status()
    elif mtype == "hs_election":
        handle_hs_message(msg)
    elif mtype == "auction_update":
        auction = msg.get("auction")
        with auctions_lock:
            auctions[auction["id"]] = auction
        print_status()
    elif mtype == "client_join":
        client_addr = tuple(msg.get("client"))
        with clients_lock:
            clients[client_addr] = time.time()
        print_status()

# --------------------------- CLIENT HANDLER ---------------------------
def handle_client_message(msg, addr):
    new_client = False
    with clients_lock:
        if addr not in clients:
            new_client = True
        clients[addr] = time.time()
    if new_client:
        response = {"status": "ok"}
        send_unicast(addr[0], addr[1], json.dumps(response))

# --------------------------- MAIN LISTENER ---------------------------
def main_listener():
    while True:
        try:
            data, addr = server_sock.recvfrom(4096)
            msg = json.loads(data.decode())
            if "type" not in msg:
                continue
            if msg["type"] in ["create_auction","bid","get_auctions"]:
                handle_client_message(msg, addr)
            else:
                handle_server_message(msg, addr)
        except:
            continue

# --------------------------- START SERVER ---------------------------
if __name__ == "__main__":
    print(f"[INFO] Server starting with ID {server_id} on port {SERVER_PORT}")
    threading.Thread(target=discovery_listener, daemon=True).start()
    threading.Thread(target=announce_presence, daemon=True).start()
    threading.Thread(target=snapshot_sender, daemon=True).start()
    threading.Thread(target=snapshot_request_loop, daemon=True).start()
    threading.Thread(target=heartbeat_sender, daemon=True).start()
    threading.Thread(target=heartbeat_checker, daemon=True).start()
    threading.Thread(target=main_listener, daemon=True).start()

    time.sleep(1)
    print("[INFO] Server running...")

    while True:
        time.sleep(1)
