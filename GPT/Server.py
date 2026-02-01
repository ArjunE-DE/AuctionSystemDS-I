import socket
import struct
import threading
import json
import random
import time

# ------------------------------
# Configuration
# ------------------------------
MCAST_GRP = "224.1.1.1"
MCAST_PORT = 5000
SERVER_PORT = random.randint(5001, 5010)
SERVER_ID = random.randint(1000, 9999)

STATE = {"items": []}
LEADER = None
KNOWN_SERVERS = {}        # port -> server_id
LAST_HEARTBEAT = {}       # port -> timestamp

# HS election state
HS_ACTIVE = False
HS_PHASE = 0
HS_EPOCH = 0
HS_RING = []              # [(port, server_id)]
HS_RETURNED = {}          # {phase: {'LEFT': False, 'RIGHT': False}}
HS_MAX_CANDIDATE = None   # max candidate seen in current phase

# Membership stability
STABLE_DELAY = 1  # seconds
last_membership_change = time.time()

# Timing
DISCOVERY_INTERVAL = 1
HEARTBEAT_INTERVAL = 2
HEARTBEAT_TIMEOUT = 6
STATUS_INTERVAL = 10
STARTUP_WAIT = 2  # seconds to wait for leader discovery

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

print(f"[{SERVER_PORT}] 🚀 Server started | ID={SERVER_ID}")

# ------------------------------
# Utilities
# ------------------------------
def send_to_multicast(msg):
    mcast_sock.sendto(msg.encode(), (MCAST_GRP, MCAST_PORT))

def send_to_server(port, msg):
    try:
        server_sock.sendto(msg.encode(), ("127.0.0.1", port))
    except Exception as e:
        print(f"[{SERVER_PORT}] ⚠️ Failed to send to {port}: {e}")

def send_hello():
    send_to_multicast(json.dumps({
        "type": "HELLO",
        "server_port": SERVER_PORT,
        "server_id": SERVER_ID
    }))

def send_server_snapshot(to_port):
    msg = {
        "type": "SERVER_SNAPSHOT",
        "leader": LEADER,
        "servers": KNOWN_SERVERS,
        "epoch": HS_EPOCH
    }
    send_to_server(to_port, json.dumps(msg))
    print(f"[{SERVER_PORT}] 🗂 Sent snapshot to {to_port} | Leader={LEADER}")

# ------------------------------
# Ring helpers (deterministic)
# ------------------------------
def compute_ring():
    items = list(KNOWN_SERVERS.items()) + [(SERVER_PORT, SERVER_ID)]
    unique_items = {}
    for port, sid in items:
        unique_items[sid] = port
    return sorted([(port, sid) for sid, port in unique_items.items()], key=lambda x: x[1])

def ring_neighbors(ring=None):
    ring = ring or HS_RING
    n = len(ring)
    if n == 1:
        return SERVER_PORT, SERVER_PORT
    ports_sorted = [port for port, _ in ring]
    idx = ports_sorted.index(SERVER_PORT)
    left = ports_sorted[(idx - 1) % n]
    right = ports_sorted[(idx + 1) % n]
    return left, right

# ------------------------------
# Hirschberg–Sinclair Election
# ------------------------------
HS_ACTIVE = False
HS_PHASE = 0
HS_EPOCH = 0
HS_RING = []              # [(port, server_id)]
HS_RETURNED = {}          # {phase: {'LEFT': False, 'RIGHT': False}}
HS_MAX_CANDIDATE = None   # max candidate seen in current phase
HS_LEFT_PORT = {}         # left port per phase
HS_RIGHT_PORT = {}        # right port per phase

def maybe_start_hs(force=False):
    global last_membership_change
    if HS_ACTIVE:
        return
    if LEADER and not force:
        return
    if not force and (time.time() - last_membership_change < STABLE_DELAY):
        return
    start_hs_election()

def start_hs_election():
    global HS_ACTIVE, HS_PHASE, HS_EPOCH, HS_RING, HS_RETURNED, HS_MAX_CANDIDATE
    if HS_ACTIVE:
        return
    HS_ACTIVE = True
    HS_PHASE = 0
    HS_EPOCH += 1
    HS_RING = compute_ring()
    HS_RETURNED = {HS_PHASE: {'LEFT': False, 'RIGHT': False}}
    HS_MAX_CANDIDATE = SERVER_ID

    left, right = ring_neighbors()
    HS_LEFT_PORT[HS_PHASE] = left
    HS_RIGHT_PORT[HS_PHASE] = right

    print(f"[{SERVER_PORT}] 🔁 HS START/JOIN | Epoch={HS_EPOCH} | Ring={[sid for _, sid in HS_RING]}")
    if len(HS_RING) == 1:
        elect_self()
        return
    send_hs_messages(SERVER_ID, HS_PHASE)

def send_hs_messages(candidate, phase):
    left = HS_LEFT_PORT[phase]
    right = HS_RIGHT_PORT[phase]
    hops = 2 ** phase

    base_msg = {
        "type": "ELECTION",
        "candidate": candidate,
        "phase": phase,
        "epoch": HS_EPOCH,
        "hop_count": 0,
        "max_hops": hops,
        "origin_id": SERVER_ID,
        "origin_direction": None,  # for replies
        "sender_port": SERVER_PORT
    }

    print(f"[{SERVER_PORT}] 📤 Sent candidate {candidate} dir=LEFT to {left} hops={hops}")
    print(f"[{SERVER_PORT}] 📤 Sent candidate {candidate} dir=RIGHT to {right} hops={hops}")

    send_to_server(left, json.dumps({**base_msg, "direction": "LEFT", "origin_direction": "LEFT"}))
    send_to_server(right, json.dumps({**base_msg, "direction": "RIGHT", "origin_direction": "RIGHT"}))

def handle_election(msg):
    global HS_MAX_CANDIDATE
    candidate = msg["candidate"]
    phase = msg["phase"]
    epoch = msg["epoch"]
    direction = msg["direction"]
    hop_count = msg["hop_count"]
    max_hops = msg["max_hops"]
    origin_id = msg["origin_id"]
    origin_direction = msg["origin_direction"]
    sender_port = msg["sender_port"]

    if epoch != HS_EPOCH:
        return

    # Only consider candidates >= self
    if candidate < SERVER_ID:
        candidate = SERVER_ID

    if candidate > HS_MAX_CANDIDATE:
        HS_MAX_CANDIDATE = candidate

    hop_count += 1
    msg["candidate"] = candidate
    msg["hop_count"] = hop_count
    msg["sender_port"] = SERVER_PORT
    msg["origin_direction"] = origin_direction

    if hop_count < max_hops:
        # Forward in same direction
        next_hop = ring_neighbors()[0] if direction == "LEFT" else ring_neighbors()[1]
        send_to_server(next_hop, json.dumps(msg))
        print(f"[{SERVER_PORT}] ➡️ Forwarding {candidate} dir={direction} hop_count={hop_count}")
    else:
        # Max hops reached → send REPLY along reverse path
        send_reply(msg, sender_port)

def send_reply(msg, return_port):
    reply = {
        "type": "REPLY",
        "candidate": msg["candidate"],
        "phase": msg["phase"],
        "epoch": msg["epoch"],
        "origin_id": msg["origin_id"],
        "return_port": return_port,
        "origin_direction": msg["origin_direction"]
    }
    send_to_server(return_port, json.dumps(reply))
    print(f"[{SERVER_PORT}] 📩 REPLY sent for candidate {msg['candidate']} back to {return_port} (dir={msg['origin_direction']})")

def handle_reply(msg):
    global HS_PHASE, HS_ACTIVE, HS_MAX_CANDIDATE
    candidate = msg["candidate"]
    phase = msg["phase"]
    epoch = msg["epoch"]
    origin_id = msg["origin_id"]
    return_port = msg["return_port"]
    origin_direction = msg.get("origin_direction")

    if epoch != HS_EPOCH:
        return

    # Forward if not origin
    if origin_id != SERVER_ID:
        send_to_server(return_port, json.dumps(msg))
        print(f"[{SERVER_PORT}] ➡️ Forwarding REPLY for {candidate} back to {return_port}")
        return

    # Origin node → mark LEFT/RIGHT based on origin_direction
    if origin_direction == "LEFT":
        HS_RETURNED[phase]['LEFT'] = True
    elif origin_direction == "RIGHT":
        HS_RETURNED[phase]['RIGHT'] = True
    else:
        print(f"[{SERVER_PORT}] 📬 REPLY received from UNKNOWN for candidate {candidate}")

    if candidate > HS_MAX_CANDIDATE:
        HS_MAX_CANDIDATE = candidate

    print(f"[{SERVER_PORT}] 📬 REPLY received for candidate {candidate} dir={origin_direction}")
    check_phase_completion()

def check_phase_completion():
    global HS_PHASE, HS_ACTIVE
    returned = HS_RETURNED.get(HS_PHASE, {})
    if returned.get('LEFT') and returned.get('RIGHT'):
        if HS_MAX_CANDIDATE == SERVER_ID:
            elect_self()
        else:
            HS_PHASE += 1
            HS_RETURNED[HS_PHASE] = {'LEFT': False, 'RIGHT': False}
            left, right = ring_neighbors()
            HS_LEFT_PORT[HS_PHASE] = left
            HS_RIGHT_PORT[HS_PHASE] = right
            print(f"[{SERVER_PORT}] 🔁 HS phase {HS_PHASE} start")
            send_hs_messages(HS_MAX_CANDIDATE, HS_PHASE)


def elect_self():
    global LEADER, HS_ACTIVE
    LEADER = SERVER_ID
    HS_ACTIVE = False
    print(f"[{SERVER_PORT}] 👑 I AM LEADER")
    broadcast_leader()

def broadcast_leader():
    msg = {"type": "LEADER", "leader": SERVER_ID}
    for port in KNOWN_SERVERS:
        if port != SERVER_PORT:
            send_to_server(port, json.dumps(msg))
            send_server_snapshot(port)

# ------------------------------
# Multicast listener
# ------------------------------
def multicast_listener():
    global last_membership_change
    while True:
        try:
            data, _ = mcast_sock.recvfrom(1024)
            msg = json.loads(data.decode())
        except:
            continue
        if msg.get("type") != "HELLO":
            continue
        port = msg["server_port"]
        sid = msg["server_id"]
        if port == SERVER_PORT:
            continue
        is_new = port not in KNOWN_SERVERS
        KNOWN_SERVERS[port] = sid
        LAST_HEARTBEAT[port] = time.time()
        if is_new:
            last_membership_change = time.time()
            print(f"[{SERVER_PORT}] ➕ New server joined ({sid})")
            if LEADER is not None:
                send_to_server(port, json.dumps({"type":"LEADER","leader":LEADER}))
                send_server_snapshot(port)

# ------------------------------
# Leader monitor
# ------------------------------
def leader_monitor():
    global LEADER
    while True:
        if LEADER and LEADER != SERVER_ID:
            leader_port = next((p for p, sid in KNOWN_SERVERS.items() if sid == LEADER), None)
            if not leader_port or time.time() - LAST_HEARTBEAT.get(leader_port,0) > HEARTBEAT_TIMEOUT:
                print(f"[{SERVER_PORT}] 💀 Leader {LEADER} dead → triggering HS")
                if leader_port:
                    KNOWN_SERVERS.pop(leader_port, None)
                    LAST_HEARTBEAT.pop(leader_port, None)
                LEADER = None
                HS_ACTIVE = False
                maybe_start_hs(force=True)
        time.sleep(HEARTBEAT_INTERVAL)

# ------------------------------
# Server listener
# ------------------------------
def server_listener():
    global LEADER
    while True:
        try:
            data, addr = server_sock.recvfrom(4096)
            msg = json.loads(data.decode())
        except:
            continue
        if msg["type"] == "ELECTION":
            handle_election(msg)
        elif msg["type"] == "REPLY":
            handle_reply(msg)
        elif msg["type"] == "LEADER":
            incoming_leader = msg["leader"]
            if LEADER is None:
                LEADER = incoming_leader
                print(f"[{SERVER_PORT}] 👑 Leader declaration received: {incoming_leader} → accepted")
            elif LEADER != incoming_leader:
                print(f"[{SERVER_PORT}] 👑 Leader declaration received: {incoming_leader} → rejected (already have {LEADER})")
        elif msg["type"] == "SERVER_SNAPSHOT":
            incoming_leader = msg.get("leader")
            snapshot = msg.get("servers", {})
            if LEADER is None:
                LEADER = incoming_leader
                print(f"[{SERVER_PORT}] 👑 Leader adopted from snapshot: {LEADER}")
            for port, sid in snapshot.items():
                if int(port) not in KNOWN_SERVERS:
                    KNOWN_SERVERS[int(port)] = sid
                    LAST_HEARTBEAT[int(port)] = time.time()
                    print(f"[{SERVER_PORT}] 🗂 Added server from snapshot: {sid} on port {port}")

# ------------------------------
# Periodic hello
# ------------------------------
def periodic_hello():
    while True:
        send_hello()
        time.sleep(DISCOVERY_INTERVAL)

# ------------------------------
# Status printer
# ------------------------------
def print_status():
    while True:
        print(f"\n[{SERVER_PORT}] STATUS")
        print(f"ID={SERVER_ID} | Leader={LEADER} | Epoch={HS_EPOCH}")
        print(f"Known IDs={list(KNOWN_SERVERS.values())}")
        time.sleep(STATUS_INTERVAL)

# ------------------------------
# Main
# ------------------------------
if __name__ == "__main__":
    KNOWN_SERVERS[SERVER_PORT] = SERVER_ID
    LAST_HEARTBEAT[SERVER_PORT] = time.time()

    threading.Thread(target=multicast_listener, daemon=True).start()
    threading.Thread(target=server_listener, daemon=True).start()
    threading.Thread(target=periodic_hello, daemon=True).start()
    threading.Thread(target=leader_monitor, daemon=True).start()
    threading.Thread(target=print_status, daemon=True).start()

    # Wait for leader discovery
    print(f"[{SERVER_PORT}] ⏳ Waiting {STARTUP_WAIT}s for leader discovery...")
    start = time.time()
    while time.time() - start < STARTUP_WAIT:
        time.sleep(0.1)
        if LEADER is not None:
            print(f"[{SERVER_PORT}] 👑 Leader detected during startup: {LEADER}")
            break

    if LEADER is None:
        print(f"[{SERVER_PORT}] ⚡ No leader found → triggering HS election")
        maybe_start_hs()

    while True:
        time.sleep(1)
