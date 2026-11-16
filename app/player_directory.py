"""Known permission IDs, kept separately from the live player count."""

import json
import logging
import re
import time

from .config import atomic_json

log = logging.getLogger("valheim")
MAX_PLAYERS = 500
PREFIX = r"^(?:\d{1,2}/\d{1,2}/\d{4} \d{1,2}:\d{2}:\d{2}:\s*)?"
STEAM_CONNECTION = re.compile(PREFIX + r"Got connection SteamID (\d{17})$")
PLATFORM_CONNECTION = re.compile(
    PREFIX + r"PlayFab socket with remote ID playfab/[A-Za-z0-9]+ "
    r"received local Platform ID ([A-Za-z]+_[A-Za-z0-9:_-]{1,100})$"
)


def permission_id(value):
    if re.fullmatch(r"\d{17}", value):
        value = "Steam_" + value
    if (
        len(value) > 128
        or not re.fullmatch(r"[A-Za-z]+_[A-Za-z0-9:_-]+", value)
        or value.split("_", 1)[0].casefold() == "playfab"
    ):
        raise ValueError("Use o ID completo de permissões exibido no F2, sem códigos de conexão")
    return value


def platform(player_id):
    prefix = player_id.split("_", 1)[0]
    return {"Steam": "Steam", "Xbox": "Xbox", "Nintendo": "Nintendo", "PSN": "PlayStation"}.get(
        prefix, prefix
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
            for key, value in values.items():
                try:
                    if permission_id(key) != key:
                        continue
                    if type(value) in (int, float) and re.fullmatch(r"Steam_\d{17}", key):
                        value = dict(
                            last_seen=value,
                            updated_at=value,
                            name="",
                            source="steam_log",
                            confirmed=True,
                        )
                    if not isinstance(value, dict):
                        continue
                    seen, updated = value.get("last_seen"), value.get("updated_at")
                    if (
                        (
                            seen is not None
                            and (type(seen) not in (int, float) or not 0 < seen < 10**12)
                        )
                        or type(updated) not in (int, float)
                        or not 0 < updated < 10**12
                        or value.get("source") not in ("steam_log", "platform_log", "admin")
                        or type(value.get("confirmed")) is not bool
                    ):
                        continue
                    name = self.valid_name(value.get("name", ""))
                    platform_name = self.valid_name(value.get("platform", platform(key)))
                    if len(platform_name) > 40:
                        continue
                    self.entries[key] = {
                        "last_seen": seen,
                        "updated_at": updated,
                        "name": name,
                        "source": value["source"],
                        "confirmed": value["confirmed"],
                        "platform": platform_name,
                    }
                except ValueError:
                    continue
            self.prune()
        except (OSError, ValueError):
            # Optional discovery must never prevent the server from starting.
            log.warning(
                "Não foi possível ler os IDs conhecidos; o cadastro manual continua disponível"
            )

    def prune(self):
        self.entries = dict(
            sorted(self.entries.items(), key=lambda entry: entry[1]["updated_at"], reverse=True)[
                :MAX_PLAYERS
            ]
        )

    @staticmethod
    def valid_name(value):
        if (
            not isinstance(value, str)
            or len(value) > 80
            or any(ord(c) < 32 or ord(c) == 127 for c in value)
        ):
            raise ValueError("O nome deve ter até 80 caracteres, sem quebras de linha")
        return value.strip()

    def register(self, player_id, name, platform_name=""):
        player_id = permission_id(player_id)
        name = self.valid_name(name)
        platform_name = self.valid_name(platform_name) or platform(player_id)
        if len(platform_name) > 40:
            raise ValueError("A plataforma deve ter até 40 caracteres")
        previous = self.entries.get(player_id, {})
        record = {
            "last_seen": previous.get("last_seen"),
            "updated_at": time.time(),
            "name": name,
            "source": "admin",
            "confirmed": True,
            "platform": platform_name,
        }
        updated = dict(self.entries)
        updated[player_id] = record
        updated = dict(
            sorted(updated.items(), key=lambda item: item[1]["updated_at"], reverse=True)[
                :MAX_PLAYERS
            ]
        )
        atomic_json(self.path, updated)
        self.entries = updated
        return {"id": player_id, "platform": platform(player_id), **record}

    def consume(self, line):
        match = STEAM_CONNECTION.fullmatch(line)
        crossplay = PLATFORM_CONNECTION.fullmatch(line)
        if not match and not crossplay:
            return
        try:
            player_id = permission_id("Steam_" + match[1] if match else crossplay[1])
        except ValueError:
            return
        now = time.time()
        is_new = player_id not in self.entries
        previous = self.entries.get(player_id, {})
        confirmed = bool(previous.get("confirmed") or re.fullmatch(r"Steam_\d{17}", player_id))
        self.entries[player_id] = {
            "last_seen": now,
            "updated_at": now,
            "name": previous.get("name", ""),
            "source": (
                "admin"
                if previous.get("source") == "admin"
                else "steam_log" if match else "platform_log"
            ),
            "confirmed": confirmed,
            "platform": previous.get("platform", platform(player_id)),
        }
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
        return [
            {"id": player_id, "platform": platform(player_id), **record}
            for player_id, record in self.entries.items()
        ]
