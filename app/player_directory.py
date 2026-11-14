"""Known permission IDs, kept separately from the live player count."""

import json
import logging
import re
import time

from .config import atomic_json

log = logging.getLogger("valheim")
MAX_PLAYERS = 500
# Only consume the explicit Steam identity message observed in real server logs.
# Character ZDOIDs and PlayFab connection handles are not permission IDs.
STEAM_CONNECTION = re.compile(
    r"^(?:\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2}:\s*)?Got connection SteamID (\d{17})$"
)


class PlayerDirectory:
    def __init__(self, path):
        self.path = path
        self.entries = {}
        self.last_write = 0
        try:
            values = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
            if not isinstance(values, dict):
                raise ValueError("Invalid directory")
            self.entries = {
                key: value
                for key, value in values.items()
                if re.fullmatch(r"Steam_\d{17}", key)
                and type(value) in (int, float)
                and 0 < value < 10**12
            }
            self.prune()
        except (OSError, ValueError):
            # Optional discovery must never prevent the server from starting.
            log.warning(
                "Não foi possível ler os IDs conhecidos; o cadastro manual continua disponível"
            )

    def prune(self):
        self.entries = dict(
            sorted(self.entries.items(), key=lambda entry: entry[1], reverse=True)[:MAX_PLAYERS]
        )

    def consume(self, line):
        match = STEAM_CONNECTION.fullmatch(line)
        if not match:
            return
        player_id = "Steam_" + match[1]
        now = time.time()
        is_new = player_id not in self.entries
        self.entries[player_id] = now
        self.prune()
        if is_new or now - self.last_write >= 60:
            try:
                atomic_json(self.path, self.entries)
                self.last_write = now
            except OSError:
                # Disk errors here must not stop draining the game's stdout.
                log.warning(
                    "Não foi possível persistir os IDs conhecidos; a sessão atual mantém os dados em memória"
                )

    def describe(self):
        return [{"id": player_id, "last_seen": seen} for player_id, seen in self.entries.items()]
