import json
import os
import re
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MODIFIERS = {
    "combat": ["", "veryeasy", "easy", "hard", "veryhard"],
    "deathpenalty": ["", "casual", "veryeasy", "easy", "hard", "hardcore"],
    "resources": ["", "muchless", "less", "more", "muchmore", "most"],
    "raids": ["", "none", "muchless", "less", "more", "muchmore"],
    "portals": ["", "casual", "hard", "veryhard"],
}
KEYS = ["nobuildcost", "playerevents", "passivemobs", "nomap"]


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=".write-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    server_name: str = Field(default="Valheim ARM", min_length=1, max_length=64)
    world_name: str = Field(default="Dedicated", min_length=1, max_length=64)
    server_password: str = Field(min_length=5, max_length=128)
    server_port: int = Field(default=2456, ge=1024, le=65534)
    public: bool = True
    crossplay: bool = False
    preset: str = ""
    modifier_combat: str = ""
    modifier_deathpenalty: str = ""
    modifier_resources: str = ""
    modifier_raids: str = ""
    modifier_portals: str = ""
    key_nobuildcost: bool = False
    key_playerevents: bool = False
    key_passivemobs: bool = False
    key_nomap: bool = False
    save_interval: int = Field(default=1800, ge=60, le=86400)
    native_backups: int = Field(default=4, ge=1, le=100)
    timezone: str = "America/Sao_Paulo"
    restart_at: str = "05:00"
    backup_at: str = "05:00"
    auto_update: bool = True
    update_interval: int = Field(default=1800, ge=300, le=86400)
    retain_daily: int = Field(default=7, ge=1, le=365)
    retain_weekly: int = Field(default=4, ge=1, le=52)
    bepinex: bool = False
    bepinex_url: str = ""
    bepinex_sha256: str = ""

    @field_validator("world_name")
    @classmethod
    def world(cls, value):
        if not re.fullmatch(r"[\w -]+", value) or value != value.strip():
            raise ValueError("Mundo: use letras, números, espaços internos, hífen ou sublinhado")
        return value

    @field_validator("server_name", "server_password")
    @classmethod
    def plain(cls, value):
        if any(ord(c) < 32 for c in value):
            raise ValueError("Caracteres de controle não permitidos")
        return value

    @field_validator("preset")
    @classmethod
    def valid_preset(cls, value):
        if value.lower() not in ("", "normal", "casual", "easy", "hard", "hardcore", "immersive", "hammer"):
            raise ValueError("Preset inválido")
        return value.lower()

    @field_validator("modifier_combat", "modifier_deathpenalty", "modifier_resources", "modifier_raids", "modifier_portals")
    @classmethod
    def modifier(cls, value, info):
        if value not in MODIFIERS[info.field_name.removeprefix("modifier_")]:
            raise ValueError("Modificador inválido")
        return value

    @field_validator("timezone")
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (KeyError, ValueError):
            raise ValueError("Fuso horário inválido")
        return value

    @field_validator("restart_at", "backup_at")
    @classmethod
    def schedule(cls, value):
        if value != "off" and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value):
            raise ValueError("Use HH:MM ou off")
        return value

    @model_validator(mode="after")
    def validate_pack(self):
        if self.bepinex and (not self.bepinex_url.startswith("https://") or not re.fullmatch(r"[a-fA-F0-9]{64}", self.bepinex_sha256)):
            raise ValueError("BepInEx exige URL HTTPS e SHA256 de 64 caracteres")
        return self

    def arguments(self, save_dir: Path):
        args = ["-nographics", "-batchmode", "-name", self.server_name, "-world", self.world_name,
                "-password", self.server_password, "-port", str(self.server_port),
                "-public", str(int(self.public)), "-savedir", str(save_dir),
                "-saveinterval", str(self.save_interval), "-backups", str(self.native_backups), "-logFile", "-"]
        if self.crossplay:
            args.append("-crossplay")
        if self.preset:
            args += ["-preset", self.preset]
        for key in MODIFIERS:
            value = getattr(self, "modifier_" + key)
            if value:
                args += ["-modifier", key, value]
        for key in KEYS:
            if getattr(self, "key_" + key):
                args += ["-setkey", key]
        return args


class ConfigStore:
    def __init__(self, data: Path, environ=None):
        environ = os.environ if environ is None else environ
        self.path = data / "config/overrides.json"
        self.base = {key: environ[key.upper()] for key in Settings.model_fields if key.upper() in environ}
        self.overrides = json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {}
        self.effective()  # Fail immediately, never silently discard invalid overrides.

    def effective(self):
        return Settings(**(self.base | self.overrides))

    def change(self, changes: dict, reset: list[str], confirm_world=False):
        if any(key not in Settings.model_fields for key in changes.keys() | set(reset)):
            raise ValueError("Configuração desconhecida")
        if "server_port" in changes or "server_port" in reset:
            raise ValueError("A porta é definida no Compose e exige recriar o contêiner")
        overrides = {k: v for k, v in self.overrides.items() if k not in reset} | changes
        result = Settings(**(self.base | overrides))
        if result.world_name != self.effective().world_name and not confirm_world:
            raise ValueError("Confirme a seleção/criação de outro mundo; o mundo anterior será preservado")
        atomic_json(self.path, overrides)
        self.overrides = overrides
        return result

    def describe(self, running=None):
        values = self.effective().model_dump()
        for key in ("server_password",):
            values[key] = ""  # Never return passwords to the browser.
        return {"values": values, "sources": {k: "painel" if k in self.overrides else "compose" if k in self.base else "padrão" for k in values},
                "pending": running is not None and running != self.effective(),
                "modifiers": MODIFIERS, "keys": KEYS}
