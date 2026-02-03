# multicast.py
import socket
import threading
from typing import Dict, Tuple, Callable
from config import MULTICAST_PORT, dprint
from lamport import LamportClock

class OrderedMulticast:
    def __init__(self, server_id: int, membership: Dict[int, float]):
        self.server_id = server_id
        self.membership = membership
        self.clock = LamportClock()
        self.handlers: Dict[str, Callable[[dict], None]] = {}
        self.running = False

    def register_handler(self, msg_type: str, handler: Callable[[dict], None]):
        self.handlers[msg_type] = handler

    def start_listener(self):
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", MULTICAST_PORT))
        while self.running:
            data, addr = s.recvfrom(4096)
            try:
                import json
                payload = json.loads(data.decode())
            except Exception as e:
                dprint("Multicast decode error:", e)
                continue
            ts = payload.get("ts", 0)
            self.clock.update(ts)
            mtype = payload.get("type")
            handler = self.handlers.get(mtype)
            if handler:
                handler(payload)

    def multicast(self, payload: dict):
        import json
        ts = self.clock.tick()
        payload = dict(payload)
        payload["ts"] = ts
        raw = json.dumps(payload).encode()
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # broadcast; in a real system use unicast to known members
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(raw, ("<broadcast>", MULTICAST_PORT))
