"""Restricted client for the host's Unix-socket image updater."""

import asyncio
import http.client
import json
import socket
import threading

SOCKET_PATH = "/run/valheim-update/agent.sock"


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        if not hasattr(socket, "AF_UNIX"):
            raise OSError("Unix sockets unavailable")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET_PATH)


class HostUpdates:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = {
            "available": False,
            "state": "unavailable",
            "message": "Panel updates require the host service provided by the updated installer.",
        }

    @property
    def busy(self):
        return self.current.get("state") == "running"

    def request(self, method, path, body=None):
        connection = UnixConnection("localhost", timeout=2)
        try:
            payload = json.dumps(body) if body is not None else None
            connection.request(
                method, path, body=payload, headers={"Content-Type": "application/json"}
            )
            response = connection.getresponse()
            raw = response.read(8193)
            if len(raw) > 8192:
                raise OSError("Response too large")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise OSError("Invalid response")
            if response.status not in (200, 202):
                raise ValueError(value.get("detail", "Update rejected by the host."))
            return value
        finally:
            connection.close()

    def status(self):
        with self.lock:
            try:
                self.current = self.request("GET", "/status")
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if isinstance(exc, FileNotFoundError):
                    message = "The updater connection is not installed in this container. Run the updated installer and apply its integration instructions."
                elif isinstance(exc, PermissionError):
                    message = "The panel cannot access the updater. Run the updated installer on the host."
                elif isinstance(exc, ConnectionRefusedError):
                    message = "The connection is mounted, but the host updater service is not responding. Check the systemd service."
                else:
                    message = "Updater service unavailable. Install or check the host service."
                self.current = {
                    **self.current,
                    "available": False,
                    "message": message,
                }
            return self.current.copy()

    def start(self, request_id):
        with self.lock:
            self.current = {
                "available": True,
                "state": "running",
                "message": "Confirming the request on the host…",
            }
            try:
                self.current = self.request("POST", "/update", {"request_id": request_id})
            except ValueError:
                try:
                    self.current = self.request("GET", "/status")
                except (OSError, ValueError, http.client.HTTPException):
                    pass
                raise
            return self.current.copy()

    async def run(self):
        while True:
            await asyncio.to_thread(self.status)
            await asyncio.sleep(5)
