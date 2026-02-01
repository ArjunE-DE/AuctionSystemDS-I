# discovery.py
import socket
import threading
import time
from typing import Dict
from config import BROADCAST_ADDR, DISCOVERY_PORT, DISCOVERY_INTERVAL, HEARTBEAT_INTERVAL, HEARTBEAT_TIMEOUT, dprint

class ServerDiscovery:
    def __init__(self, server_id: int, tcp_port: int):
        self.server_id = server_id
        self.tcp_port = tcp_port
        self.members: Dict[int, float] = {}  # server_id -> last_seen
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._broadcast_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()
        threading.Thread(target=self._cleanup_loop, daemon=True).start()

    def stop(self):
        self.running = False

    def _broadcast_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        while self.running:
            msg = f"SERVER_DISCOVERY {self.server_id} {self.tcp_port}"
            s.sendto(msg.encode(), (BROADCAST_ADDR, DISCOVERY_PORT))
            time.sleep(DISCOVERY_INTERVAL)

    def _listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", DISCOVERY_PORT))
        while self.running:
            data, addr = s.recvfrom(1024)
            parts = data.decode().split()
            if len(parts) >= 3 and parts[0] == "SERVER_DISCOVERY":
                sid = int(parts[1])
                if sid != self.server_id:
                    self.members[sid] = time.time()
            elif len(parts) >= 3 and parts[0] == "HEARTBEAT":
                sid = int(parts[1])
                if sid != self.server_id:
                    self.members[sid] = time.time()

    def _cleanup_loop(self):
        while self.running:
            now = time.time()
            removed = []
            for sid, last in list(self.members.items()):
                if now - last > HEARTBEAT_TIMEOUT:
                    removed.append(sid)
                    del self.members[sid]
            for sid in removed:
                dprint(f"[Server {self.server_id}] Removed dead server {sid}")
            time.sleep(HEARTBEAT_INTERVAL)

    def send_heartbeat(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"HEARTBEAT {self.server_id} {self.tcp_port}"
        s.sendto(msg.encode(), (BROADCAST_ADDR, DISCOVERY_PORT))


class ClientDiscovery:
    """Client listens for SERVER_DISCOVERY and chooses a server."""
    def __init__(self):
        self.server_addr = None  # (ip, port)
        self.running = False

    def start(self):
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", DISCOVERY_PORT))
        while self.running and self.server_addr is None:
            data, addr = s.recvfrom(1024)
            parts = data.decode().split()
            if len(parts) >= 3 and parts[0] == "SERVER_DISCOVERY":
                tcp_port = int(parts[2])
                self.server_addr = (addr[0], tcp_port)
                dprint(f"[Client] Discovered server at {self.server_addr}")
                break
