# run_server.py
import sys
import time
from server import AuctionServer

def main():
    if len(sys.argv) != 2:
        print("Usage: python run_server.py <server_id_int>")
        return
    sid = int(sys.argv[1])
    srv = AuctionServer(sid)
    srv.start()
    print(f"Server {sid} running. Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("Stopping server...")

if __name__ == "__main__":
    main()
