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
            raise OSError("Sockets Unix indisponíveis")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(SOCKET_PATH)


class HostUpdates:
    def __init__(self):
        self.lock = threading.Lock()
        self.current = {
            "available": False,
            "state": "unavailable",
            "message": "Atualização pelo painel requer preparar o serviço no host com o instalador atualizado.",
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
                raise OSError("Resposta excessiva")
            value = json.loads(raw)
            if not isinstance(value, dict):
                raise OSError("Resposta inválida")
            if response.status not in (200, 202):
                raise ValueError(value.get("detail", "Atualização não aceita pelo host."))
            return value
        finally:
            connection.close()

    def status(self):
        with self.lock:
            try:
                self.current = self.request("GET", "/status")
            except (OSError, ValueError, http.client.HTTPException) as exc:
                if isinstance(exc, FileNotFoundError):
                    message = "A conexão com o atualizador não está instalada neste contêiner. Execute o instalador atualizado e aplique a integração indicada por ele."
                elif isinstance(exc, PermissionError):
                    message = "O painel não tem permissão para acessar o atualizador. Reaplique o instalador atualizado no host."
                elif isinstance(exc, ConnectionRefusedError):
                    message = "A conexão está montada, mas o serviço de atualização não está respondendo no host. Verifique o serviço systemd."
                else:
                    message = "Serviço de atualização indisponível. Prepare ou verifique o serviço no host."
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
                "message": "Confirmando solicitação no host…",
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
