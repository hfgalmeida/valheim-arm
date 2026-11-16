"""Bounded startup evidence and per-game FEX compatibility retries."""

import json
import logging
import math
import os
import re
import time
import uuid
from pathlib import Path

from .config import atomic_json

LIMIT = 20
COMPATIBILITY_ENV = {
    "FEX_SMCCHECKS": "full",
    "FEX_MONOHACKS": "0",
    "FEX_MULTIBLOCK": "0",
    "FEX_DISKCACHE": "0",
    "FEX_TSOENABLED": "1",
}


class StartupDiagnostics:
    def __init__(self, data):
        self.path = data / "config/startup-history.json"
        self.history = []
        self.active = None
        self.compatibility_requested = False
        self.error = None
        try:
            if self.path.exists():
                if self.path.stat().st_size > 128 * 1024:
                    raise ValueError("Startup history too large")
                records = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(records, list):
                    raise ValueError("Invalid startup history")
                # Only retain our bounded, non-secret scalar schema.
                for item in records[-LIMIT:]:
                    if (
                        not isinstance(item, dict)
                        or type(item.get("started")) not in (int, float)
                        or not 0 < item["started"] < 10**12
                    ):
                        continue
                    keys = (
                        "id",
                        "started",
                        "finished",
                        "ready_at",
                        "profile",
                        "crossplay",
                        "bepinex",
                        "phase",
                        "outcome",
                        "signal",
                        "signal_code",
                        "signature",
                        "exit_code",
                        "fex_version",
                    )
                    clean = {
                        key: item[key]
                        for key in keys
                        if key in item and type(item[key]) in (str, int, float, bool, type(None))
                    }
                    if any(isinstance(value, str) and len(value) > 128 for value in clean.values()):
                        continue
                    if any(
                        type(value) in (int, float) and not math.isfinite(value)
                        for value in clean.values()
                    ):
                        continue
                    if clean.get("outcome") in ("starting", "ready"):
                        clean["outcome"] = "interrupted"
                    self.history.append(clean)
        except (OSError, ValueError):
            self.error = "Histórico de inicialização indisponível; confira espaço e permissões"

    def save(self):
        try:
            atomic_json(self.path, self.history[-LIMIT:])
            self.error = None
        except OSError:
            self.error = "Não foi possível persistir o diagnóstico de inicialização"
            logging.getLogger("valheim").warning(self.error)

    def environment(self):
        mode = os.environ.get("FEX_GAME_MODE", "auto")
        if mode not in ("auto", "standard", "compatible"):
            raise ValueError("FEX_GAME_MODE deve ser auto, standard ou compatible")
        compatible = mode == "compatible" or (mode == "auto" and self.compatibility_requested)
        env = os.environ.copy()
        if compatible:
            env.update(COMPATIBILITY_ENV)
        return ("compatible" if compatible else "standard"), env

    def begin(self, profile, settings):
        version = "unknown"
        try:
            value = json.loads(Path("/opt/build-info.json").read_text())["fex_version"]
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", value):
                version = value
        except (OSError, ValueError, KeyError, TypeError):
            pass
        self.active = {
            "id": uuid.uuid4().hex,
            "started": time.time(),
            "finished": None,
            "profile": profile,
            "crossplay": settings.crossplay,
            "bepinex": settings.bepinex,
            "phase": "before_scene",
            "outcome": "starting",
            "fex_version": version,
        }
        self.history.append(self.active)
        self.history = self.history[-LIMIT:]
        self.save()

    def observe(self, line, watchdog):
        if self.active is None:
            return
        changed = False
        phase = (
            "unknown"
            if watchdog.phase_unknown
            else (
                "world"
                if watchdog.world_started
                else "scene" if watchdog.scene_started else "before_scene"
            )
        )
        if self.active["phase"] != phase and self.active.get("outcome") != "ready":
            self.active["phase"] = phase
            changed = True
        match = re.match(r"^Caught fatal signal - signo:(\d+) code:(-?\d+)", line)
        if match and watchdog.fatal and "signal" not in self.active:
            self.active.update(signal=int(match[1]), signal_code=int(match[2]), outcome="fatal")
            changed = True
        if watchdog.fatal and re.fullmatch(
            r"#\d+\s+0x[0-9a-fA-F]+\s+in PlatformInitializer:EarlyInitialize \(\)", line
        ):
            if self.active.get("signature") != "platform_initializer":
                self.active["signature"] = "platform_initializer"
                changed = True
        if changed:
            self.save()

    def ready(self):
        if self.active is not None:
            self.active.update(outcome="ready", phase="ready", ready_at=time.time())
            self.save()

    def finish(self, code, watchdog, expected=False, spawn_failed=False):
        early_fatal = watchdog.safe_to_terminate or (
            code in (-4, -6, -7, -8, -11)
            and watchdog.scene_started
            and not watchdog.world_started
            and not watchdog.phase_unknown
        )
        if early_fatal:
            self.compatibility_requested = True
        if self.active is None:
            return
        self.active.update(finished=time.time(), exit_code=code)
        self.active["outcome"] = (
            "spawn_failed"
            if spawn_failed
            else (
                "fatal"
                if watchdog.fatal
                else "stopped" if expected else "exited" if code == 0 else "crashed"
            )
        )
        self.save()
        self.active = None

    def describe(self):
        return {
            "history": [dict(item) for item in reversed(self.history)],
            "compatibility_next": self.compatibility_requested,
            "error": self.error,
        }
