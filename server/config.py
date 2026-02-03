import os

# Set SELF_URL for each server on startup
SELF_URL = os.getenv("SELF_URL")
if SELF_URL is None:
    raise RuntimeError("SELF_URL environment variable not set!")

# Cluster servers
SERVERS = {
    "server1": "http://localhost:8001",
    "server2": "http://localhost:8002",
    "server3": "http://localhost:8003"
}
