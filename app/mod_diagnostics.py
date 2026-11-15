"""Session-scoped BepInEx log evidence; never execute or infer DLL identities."""

import os
import re
import time
from collections import deque
from pathlib import Path

LOG = re.compile(
    r"^\[(?P<level>Info|Message|Warning|Error|Fatal|Debug)\s*:\s*(?P<source>[^]\r\n]{1,120})\]\s*(?P<message>.*)$",
    re.IGNORECASE,
)
IDENTITY = (
    r"\[(?P<name>[^]\r\n]{1,180}) (?P<version>[0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?)\]"
)


class ModDiagnostics:
    def __init__(self, data):
        self.root = data / "mods/BepInEx/plugins"
        self.begin(False, snapshot=False)

    def fingerprint(self, path):
        stat = path.stat()
        return (stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def begin(self, enabled, snapshot=True):
        self.enabled = enabled
        self.started = time.time() if snapshot else None
        self.version = None
        self.complete = False
        self.plugins = {}
        self.issues = deque(maxlen=50)
        self.baseline = {}
        self.scan_error = None
        if not snapshot:
            return
        try:
            if any(path.is_symlink() for path in (self.root, *self.root.parents)):
                raise ValueError

            def scan_failed(error):
                raise error

            for base, dirs, files in os.walk(self.root, followlinks=False, onerror=scan_failed):
                dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
                for name in files:
                    path = Path(base) / name
                    if path.suffix.lower() == ".dll" and not path.is_symlink():
                        if len(self.baseline) >= 2000:
                            raise ValueError
                        self.baseline[path.relative_to(self.root).as_posix()] = self.fingerprint(
                            path
                        )
        except (OSError, ValueError):
            self.scan_error = "Não foi possível comparar todas as DLLs com o início da sessão."

    def consume(self, line):
        if not self.enabled:
            return
        match = LOG.match(line[:4000].lstrip("\ufeff"))
        if not match:
            return
        level, source, message = match.group("level", "source", "message")
        source, level = source.strip(), level.lower()
        loader = source.lower() in {"bepinex", "chainloader", "preloader"}
        if loader:
            version = re.match(
                r"BepInEx ([0-9]+(?:\.[0-9]+){1,3}(?:[-+][A-Za-z0-9._-]+)?)(?:\s|$)", message
            )
            if version:
                self.version = version[1]
            loading = re.match(
                r"Loading " + IDENTITY + r"(?: \([A-Za-z0-9._-]{1,200}\))?$", message
            )
            failure = re.match(r"(?:Error loading|Could not load|Skipping) " + IDENTITY, message)
            identity = loading or failure
            if identity:
                key = (identity["name"], identity["version"])
                if key in self.plugins or len(self.plugins) < 200:
                    previous = self.plugins.get(key, {})
                    failed = bool(failure) or previous.get("state") == "failed"
                    self.plugins[key] = {
                        "name": key[0],
                        "version": key[1],
                        "state": "failed" if failed else "loading_reported",
                    }
            if re.fullmatch(
                r"Chainloader startup complete(?: \([0-9]+ loaded, [0-9]+ skipped, [0-9]+ failed\))?",
                message,
            ):
                self.complete = True
            if level in {"warning", "error", "fatal"}:
                self.add_issue(level, source, message)
        elif level in {"error", "fatal"} and self.version:
            # Other sources may be Unity or plugins. Report without attributing causality.
            self.add_issue(level, source, message)

    def add_issue(self, level, source, message):
        entry = {"level": level, "source": source, "message": message[:1000]}
        if not any(all(item[key] == value for key, value in entry.items()) for item in self.issues):
            self.issues.append(entry | {"time": time.time()})

    def file_state(self, path, live):
        if not self.started:
            return "awaiting_start"
        if self.scan_error:
            return "unknown"
        name = path.relative_to(self.root).as_posix()
        try:
            unchanged = self.baseline.get(name) == self.fingerprint(path)
        except OSError:
            return "unknown"
        if not unchanged:
            return "pending_restart"
        return "present_at_start" if live else "last_session"

    def describe(self, live, configured):
        if not self.started:
            state = "awaiting_start" if configured else "disabled"
        elif not live:
            state = "stopped"
        elif not self.enabled:
            state = "disabled"
        elif self.complete:
            state = "complete"
        elif self.version:
            state = "loading"
        else:
            state = "unconfirmed"
        return {
            "state": state,
            "enabled_for_session": self.enabled,
            "configured": configured,
            "pending_config": self.started is not None and self.enabled != configured,
            "started": self.started,
            "version": self.version,
            "complete": self.complete,
            "plugins": list(self.plugins.values()),
            "issues": list(self.issues),
            "scan_error": self.scan_error,
        }
