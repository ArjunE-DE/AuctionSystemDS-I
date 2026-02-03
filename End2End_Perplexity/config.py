# config.py
BROADCAST_ADDR = "<broadcast>"
DISCOVERY_PORT = 8000

SERVER_BASE_PORT = 8001          # each server uses SERVER_BASE_PORT + server_id

CLIENT_PORT = 52000
DISCOVERY_INTERVAL = 3.0          # seconds
HEARTBEAT_INTERVAL = 2.0
HEARTBEAT_TIMEOUT = 6.0

ELECTION_PORT = 8005

MULTICAST_PORT = 8006

AUCTION_DURATION_SECONDS = 60     # default auction runtime

# convenience: enable debug printing
DEBUG = True
def dprint(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)
