# client.py
import socket
from discovery import ClientDiscovery
from config import dprint

class AuctionClient:
    def __init__(self):
        self.discovery = ClientDiscovery()
        self.server_addr = None

    def connect(self):
        self.discovery.start()
        while self.discovery.server_addr is None:
            pass
        self.server_addr = self.discovery.server_addr
        dprint(f"[Client] Connecting to {self.server_addr}")
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.connect(self.server_addr)
        return s

    def run(self):
        conn = self.connect()
        try:
            while True:
                line = self._recv_line(conn)
                if not line:
                    break
                print(line)
                if line.startswith("COMMAND"):
                    cmd = input("> ").strip()
                    self._send(conn, cmd)
                elif line.endswith(":"):
                    # prompt from server
                    ans = input("> ").strip()
                    self._send(conn, ans)
        finally:
            conn.close()

    def _send(self, conn, msg: str):
        conn.sendall((msg + "\n").encode())

    def _recv_line(self, conn) -> str:
        data = b""
        while True:
            chunk = conn.recv(1)
            if not chunk:
                break
            if chunk == b"\n":
                break
            data += chunk
        return data.decode().strip()
