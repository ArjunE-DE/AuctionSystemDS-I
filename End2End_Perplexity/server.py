# server.py - MODIFIED VERSION
# Changes: Non-leaders relay CREATE_AUCTION and BID to leader via multicast
# Leader processes locally + multicasts to all

import socket
import threading
import time
import json
from typing import Tuple
from config import SERVER_BASE_PORT, HEARTBEAT_INTERVAL, dprint, MULTICAST_PORT
from models import AuctionState
from discovery import ServerDiscovery
from election import HirschbergSinclairElection
from multicast import OrderedMulticast

class AuctionServer:
    def __init__(self, server_id: int):
        self.server_id = server_id
        self.port = SERVER_BASE_PORT + server_id
        self.state = AuctionState()
        self.discovery = ServerDiscovery(server_id, self.port)
        self.election = HirschbergSinclairElection(server_id, self.discovery.members)
        self.multicast = OrderedMulticast(server_id, self.discovery.members)
        self.is_leader = False
        self.running = False

        # register multicast handlers
        self.multicast.register_handler("CREATE_AUCTION", self._handle_create_auction)
        self.multicast.register_handler("BID", self._handle_bid)

    def start(self):
        self.discovery.start()
        self.election.start_listener()
        self.multicast.start_listener()
        self.running = True

        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        threading.Thread(target=self._leader_monitor_loop, daemon=True).start()
        threading.Thread(target=self._tcp_server_loop, daemon=True).start()

    def _heartbeat_loop(self):
        while self.running:
            self.discovery.send_heartbeat()
            time.sleep(HEARTBEAT_INTERVAL)

    def _leader_monitor_loop(self):
        """Periodically run election if no leader chosen."""
        while self.running:
            if self.election.leader_id is None:
                self.election.run_election()
            self.is_leader = (self.election.leader_id == self.server_id)
            time.sleep(5)

    def _tcp_server_loop(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("", self.port))
        s.listen(5)
        dprint(f"[Server {self.server_id}] Listening on {self.port}")
        while self.running:
            conn, addr = s.accept()
            threading.Thread(target=self._client_handler, args=(conn, addr), daemon=True).start()

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

    def _client_handler(self, conn, addr: Tuple[str, int]):
        self._send(conn, "WELCOME TO AUCTION SYSTEM")
        current_client = None

        while True:
            self._send(conn, """Welcome to the Auction System
            1. Register
            2. Login
            3. List active auctions
            4. Create an auction
            5. Bid on an item
            6. List my purchases
            7. Quit""")

            self._send(conn, "Enter your choice (1-6):")
            cmd = self._recv_line(conn)
            if not cmd:
                break

            if cmd == "1":
                self._send(conn, "Enter name:")
                name = self._recv_line(conn)
                self._send(conn, "Enter password:")
                pwd = self._recv_line(conn)
                c = self.state.create_client(name, pwd)
                self._send(conn, f"Registered with id {c.id}")
            elif cmd == "2":
                self._send(conn, "Enter name:")
                name = self._recv_line(conn)
                self._send(conn, "Enter password:")
                pwd = self._recv_line(conn)
                c = self.state.authenticate(name, pwd)
                if c:
                    current_client = c
                    self._send(conn, f"Login successful. Your id: {c.id}")
                else:
                    self._send(conn, "Login failed.")
            elif cmd == "3":
                items = self.state.list_active_items()
                if not items:
                    self._send(conn, "No active auctions.")
                else:
                    for it in items:
                        self._send(conn, f"{it.id} | {it.name} | auction owner: {self.state.retrieve_client_name(it.owner_id)} | current bid: {it.current_bid} | highest bidder: {self.state.retrieve_client_name(it.buyer_id)} ends: {it.end_time - time.time()}")
            elif cmd == "4":
                if not current_client:
                    self._send(conn, "Please login first.")
                    continue
                self._send(conn, "Item name:")
                name = self._recv_line(conn)
                self._send(conn, "Description:")
                desc = self._recv_line(conn)
                self._send(conn, "Start price:")
                sp = float(self._recv_line(conn))
                self._send(conn, "Auction duration seconds):")
                dur = float(self._recv_line(conn))
                # RELAY TO LEADER: any server can handle client, but leader processes
                payload = {
                    "type": "CREATE_AUCTION",
                    "owner_id": current_client.id,
                    "name": name,
                    "description": desc,
                    "start_price": sp,
                    "duration": dur,
                }
                self.multicast.multicast(payload)
                self._send(conn, "Auction create relayed to leader.")
            elif cmd == "5":
                if not current_client:
                    self._send(conn, "Please login first.")
                    continue
                self._send(conn, "Auction id:")
                aid = self._recv_line(conn)
                self._send(conn, "Bid amount:")
                amt = float(self._recv_line(conn))

                # RELAY TO LEADER: any server can handle client, but leader processes
                payload = {
                    "type": "BID",
                    "bidder_id": current_client.id,
                    "item_id": aid,
                    "amount": amt,
                }
                self.multicast.multicast(payload)
                self._send(conn, "Bid relayed to leader.")
            elif cmd == "6":
                if not current_client:
                    self._send(conn, "Please login first.")
                    continue
                purchases = self.state.list_my_purchases(current_client.id)
                if not purchases:
                    self._send(conn, "No purchases yet.")
                else:
                    self._send(conn, "Your purchases:")
                    for purchase in purchases:
                        self._send(conn, f"{purchase.id} | {purchase.name} | seller: {self.state.retrieve_client_name(purchase.owner_id)} | final price: {purchase.current_bid}")

                    self._send(conn, f"{purchases}")
            elif cmd == "7":
                break
            else:
                self._send(conn, "Unknown command.")
        conn.close()

    # Multicast handlers (executed on ALL servers; leader validates, all apply)
    def _handle_create_auction(self, payload: dict):
        # Leader validates + creates, all replicas apply
        owner_id = payload["owner_id"]
        name = payload["name"]
        desc = payload["description"]
        sp = float(payload["start_price"])
        dur = float(payload["duration"])
        item = self.state.create_auction(owner_id, name, desc, sp, dur)
        dprint(f"[Server {self.server_id}] Created auction {item.id}")

    def _handle_bid(self, payload: dict):
        # Leader validates + applies, all replicas apply same
        bidder_id = payload["bidder_id"]
        item_id = payload["item_id"]

        amt = float(payload["amount"])
        ok = self.state.place_bid(bidder_id, item_id, amt)
        dprint(f"self.state.items")
        if ok:
            dprint(f"[Server {self.server_id}] Bid accepted on {item_id} ({amt})")
        else:
            dprint(f"[Server {self.server_id}] Bid rejected on {item_id} ({amt})")
