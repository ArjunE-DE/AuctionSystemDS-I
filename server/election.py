import requests
from config import SELF_URL, SERVERS
import threading
import time

leader_url = None  # global leader URL

def get_id(url: str) -> int:
    return int(url.split(":")[-1])  # extract port as ID

SELF_ID = get_id(SELF_URL)
PEERS = [url for url in SERVERS.values() if url != SELF_URL]

def start_election():
    """Bully-style leader election"""
    global leader_url
    alive = []

    for peer in PEERS:
        try:
            requests.get(f"{peer}/health", timeout=1)
            alive.append(peer)
        except:
            pass

    alive.append(SELF_URL)
    leader_url = max(alive, key=get_id)
    print(f"[Election] New leader elected: {leader_url}")
    announce_leader()


def announce_leader():
    """Notify all peers who the leader is"""
    for peer in PEERS:
        try:
            requests.post(f"{peer}/leader", json={"leader": leader_url}, timeout=1)
        except:
            pass

def is_leader() -> bool:
    return leader_url == SELF_URL

def monitor_leader(interval: int = 5):
    """Check leader health and start election if down"""
    global leader_url
    while True:
        time.sleep(interval)
        if leader_url is None or leader_url == SELF_URL:
            continue
        try:
            requests.get(f"{leader_url}/health", timeout=1)
        except:
            start_election()

def start_monitor():
    t = threading.Thread(target=monitor_leader, daemon=True)
    t.start()
