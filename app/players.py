"""Conservative log snapshots, corroborated with A2S on the Steam backend."""

import re
import socket
import time


SNAPSHOT_TTL = 660
IDLE_SECONDS = 120
# Match the complete game line, never text embedded in a player name or error.
LOG_PREFIX = r"^(?:\d{1,2}/\d{1,2}/\d{4}(?: \d{1,2}:\d{2}:\d{2})?:\s*)?"
SNAPSHOT_HEADER = re.compile(LOG_PREFIX + r"Connections\b")
SNAPSHOT = re.compile(
    LOG_PREFIX + r"Connections[ \t]+([0-9]{1,6})[ \t]+ZDOS:[0-9]+"
    r"(?:[ \t]+sent:[0-9]+[ \t]+recv:[0-9]+)?[ \t]*$"
)
CONNECTION_CHANGE = re.compile(
    r"Got connection|Got handshake|New connection|New peer connected|Got character ZDOID|"
    r"Closing socket|RPC_Disconnect|ZRpc timeout detected|Game server disconnected|"
    r"Player (?:connection lost|joined|left) server|ZPlayFabSocket::Dispose|"
    r"Keep socket for playfab/|suspend TX on playfab/|ResetParty|"
    r"Task: (?:LeaveNetworkTask|CleanPartyTask|JoinPartyTask)|"
    r"Joined PlayFab Party network|PlayFab network error|"
    r"PlayFab.*(?:connect|Connect)|k_ESteamNetworkingConnectionState_\w+|"
    r'Session ".*" with join code [0-9]{6}.* is active with [0-9]+ player\(s\)',
    re.IGNORECASE,
)


class Players:
    def __init__(self, clock=time.monotonic, wall_clock=time.time):
        self.clock = clock
        self.wall_clock = wall_clock
        self.reset()

    def reset(self):
        self.count = self.last_count = None
        self.observed = self.updated_at = None
        self.empty_since = None
        self.last_event_at = None
        self.state = "unknown"
        self.reason = "Aguardando snapshot Connections do jogo"
        self.steam = {"count": None, "updated_at": None}
        self.steam_observed = None

    def invalidate(self, reason):
        self.count = self.empty_since = None
        self.state = "transition"
        self.reason = reason
        self.last_event_at = self.wall_clock()

    def consume(self, line):
        snapshot = SNAPSHOT.fullmatch(line.strip())
        if snapshot:
            # Expiration must be checked even when no UI/idle query ran in the gap.
            self.current()
            now = self.clock()
            value = int(snapshot[1])
            if value != 0 or self.count != 0:
                self.empty_since = now if value == 0 else None
            self.count = self.last_count = value
            self.observed, self.updated_at = now, self.wall_clock()
            self.state, self.reason = "snapshot", "snapshot do jogo"
        elif SNAPSHOT_HEADER.match(line.strip()):
            self.invalidate("Formato do snapshot inválido; aguardando novo snapshot")
        elif CONNECTION_CHANGE.search(line):
            # Events invalidate, never increment/decrement or infer an empty server.
            # PlayFab's 'now N players' can describe reconnection, not final departure.
            self.invalidate("Conexão alterada; aguardando novo snapshot")

    def current(self):
        if self.observed is not None and self.clock() - self.observed >= SNAPSHOT_TTL:
            self.count = self.empty_since = None
            self.state, self.reason = "stale", "Snapshot ausente ou expirado"
        return self.count

    def idle(self, seconds=IDLE_SECONDS):
        return (
            self.current() == 0
            and self.empty_since is not None
            and self.clock() - self.empty_since >= seconds
        )

    def observe_steam(self, count):
        self.steam = {"count": count, "updated_at": self.wall_clock()}
        self.steam_observed = self.clock()
        if count is not None and self.current() is not None and count != self.count:
            self.invalidate("Consulta Steam divergente do log; aguardando novo snapshot")

    def status(self):
        count = self.current()
        now = self.clock()
        age = max(0, int(now - self.observed)) if self.observed is not None else None
        empty_for = (
            max(0, int(now - self.empty_since))
            if count == 0 and self.empty_since is not None
            else None
        )
        return {
            "count": count,
            "reason": self.reason,
            "state": self.state,
            "source": "game_log" if self.observed is not None else None,
            "updated_at": self.updated_at,
            "age_seconds": age,
            "expires_in_seconds": max(0, SNAPSHOT_TTL - age) if age is not None else None,
            "last_count": self.last_count,
            "last_event_at": self.last_event_at,
            "empty_for_seconds": empty_for,
            "idle_confirmed": self.idle(),
            "idle_wait_seconds": (
                max(0, IDLE_SECONDS - empty_for) if empty_for is not None else None
            ),
            "steam": {
                **self.steam,
                "age_seconds": (
                    max(0, int(now - self.steam_observed))
                    if self.steam_observed is not None
                    else None
                ),
            },
        }

    @staticmethod
    def stopped_status():
        return {
            "count": 0,
            "reason": "Servidor parado",
            "state": "stopped",
            "source": "supervisor",
            "updated_at": None,
            "age_seconds": None,
            "expires_in_seconds": None,
            "last_count": None,
            "last_event_at": None,
            "empty_for_seconds": None,
            "idle_confirmed": False,
            "idle_wait_seconds": None,
            "steam": {"count": None, "updated_at": None, "age_seconds": None},
        }


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
