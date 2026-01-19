import requests
from config import SERVERS

def replicate_to_peers(origin, endpoint, data):
    for name, url in SERVERS.items():
        if url == origin:
            continue

        try:
            requests.post(f"{url}{endpoint}", json=data)
        except Exception as e:
            print(f"Replication to {name} failed: {e}")
