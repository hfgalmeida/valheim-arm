"""Conservative log snapshots, corroborated with A2S on the Steam backend."""

import re
import socket
import time


class Players:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.reset()

    def reset(self):
        self.count = None
        self.observed = 0
        self.empty_since = None
        self.reason = "Aguardando snapshot Connections do jogo"

    def consume(self, line):
        snapshot = re.search(r"\bConnections (\d+) ZDOS:", line)
        if snapshot:
            value = int(snapshot[1])
            if value != 0 or self.count != 0:
                self.empty_since = self.clock() if value == 0 else None
            self.count, self.observed, self.reason = value, self.clock(), "snapshot do jogo"
        elif re.search(
            r"Got connection|Got handshake|New peer connected|Got character ZDOID|Closing socket|RPC_Disconnect|PlayFab.*(?:connect|Connect)|k_ESteamNetworkingConnectionState_(?:Connecting|Connected|Closed)",
            line,
        ):
            # A connect OR disconnect invalidates until the next full snapshot.
            self.count, self.empty_since, self.reason = (
                None,
                None,
                "Conexão alterada; aguardando novo snapshot",
            )

    def current(self):
        if self.clock() - self.observed > 660:
            self.count, self.empty_since, self.reason = None, None, "Snapshot ausente ou expirado"
        return self.count

    def idle(self, seconds=120):
        return (
            self.current() == 0
            and self.empty_since is not None
            and self.clock() - self.empty_since >= seconds
        )

    def status(self):
        return {"count": self.current(), "reason": self.reason}


def query_steam(port: int):
    """A2S_INFO, including challenge. Fragmented/malformed responses stay unknown."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as client:
            client.settimeout(2)
            request = b"\xff\xff\xff\xffTSource Engine Query\x00"
            client.sendto(request, ("127.0.0.1", port))
            data = client.recv(4096)
            if data[:5] == b"\xff\xff\xff\xffA" and len(data) == 9:
                client.sendto(request + data[5:], ("127.0.0.1", port))
                data = client.recv(4096)
            if data[:5] != b"\xff\xff\xff\xffI":
                return None
            offset = 6  # header + type + protocol
            for _ in range(4):
                offset = data.index(b"\x00", offset) + 1
            return data[offset + 2]  # appid uint16, then player count
    except (OSError, ValueError, IndexError):
        return None
