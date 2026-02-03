# election.py
import socket
import threading
import time
from typing import Optional, Dict
from config import ELECTION_PORT, dprint

class HirschbergSinclairElection:
    def __init__(self, server_id: int, all_ids: Dict[int, float]):
        self.server_id = server_id
        self.all_ids = all_ids    # membership map from discovery
        self.leader_id: Optional[int] = None
        self.running = False

    def start_listener(self):
        self.running = True
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _listen_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", ELECTION_PORT))
        while self.running:
            data, addr = s.recvfrom(1024)
            msg = data.decode().split()
            if not msg:
                continue
            if msg[0] == "LEADER_ANNOUNCE":
                leader = int(msg[1])
                self.leader_id = leader
                dprint(f"[Server {self.server_id}] Recognizes leader {leader}")

    def compute_ring_successor(self) -> Optional[int]:
        ids = sorted(list(self.all_ids.keys()) + [self.server_id])
        if len(ids) <= 1:
            return None
        idx = ids.index(self.server_id)
        return ids[(idx + 1) % len(ids)]

    def run_election(self):
        """Very simplified: just pick max id among known and broadcast it."""
        candidate = max([self.server_id] + list(self.all_ids.keys()))
        self.leader_id = candidate
        dprint(f"[Server {self.server_id}] Elected leader {candidate}")
        # Announce
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        msg = f"LEADER_ANNOUNCE {candidate}"
        s.sendto(msg.encode(), ("<broadcast>", ELECTION_PORT))
