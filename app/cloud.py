"""Optional cloud exports. Credentials live outside the directories we archive."""
import asyncio
import configparser
import io
import json
import os
import re
import tempfile
import time
from pathlib import Path, PurePosixPath

from .backups import digest
from .config import atomic_json


PROVIDERS = {"drive": "Google Drive", "onedrive": "OneDrive", "dropbox": "Dropbox"}
OPTIONS = {
    "drive": {"scope", "root_folder_id", "team_drive"},
    "onedrive": {"drive_id", "drive_type", "region"},
    "dropbox": set(),
}


def connection(text):
    """Import one OAuth remote, rejecting command hooks and filesystem backends."""
    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(text)
        if parser.defaults() or len(parser.sections()) != 1:
            raise ValueError
        values = dict(parser[parser.sections()[0]])
        provider = values.get("type")
        if provider not in PROVIDERS:
            raise ValueError
        allowed = {"type", "token", "client_id", "client_secret"} | OPTIONS[provider]
        if set(values) - allowed:
            raise ValueError
        token = json.loads(values["token"])
        if not isinstance(token, dict) or not token.get("access_token") or not token.get("refresh_token"):
            raise ValueError
        if provider == "onedrive" and not values.get("drive_id"):
            raise ValueError
    except (configparser.Error, KeyError, ValueError, TypeError):
        raise ValueError("Importe uma única conexão rclone autorizada de OneDrive, Dropbox ou Google Drive, sem opções avançadas") from None
    result = configparser.ConfigParser(interpolation=None)
    result["cloud"] = values
    output = io.StringIO()
    result.write(output)
    return provider, output.getvalue()


class Cloud:
    def __init__(self, data, backups):
        self.root = data / "cloud"
        self.root.mkdir(mode=0o700, exist_ok=True)
        self.config_path = self.root / "settings.json"
        self.receipts_path = self.root / "exports.json"
        self.settings, self.receipts = {}, {}
        try:
            self.settings = json.loads(self.config_path.read_text()) if self.config_path.exists() else {}
            self.receipts = json.loads(self.receipts_path.read_text()) if self.receipts_path.exists() else {}
            if not isinstance(self.settings, dict) or not isinstance(self.receipts, dict):
                raise ValueError
            if self.settings.get("enabled") and (not isinstance(self.settings.get("since"), (int, float))
                                                or self.settings.get("provider") not in PROVIDERS):
                raise ValueError
        except (OSError, ValueError):
            self.settings = {"enabled": False, "error": "Estado da exportação inválido; reconfigure a nuvem no painel"}
            self.receipts = {}
        filename = self.settings.get("connection", "rclone.conf")
        if not isinstance(filename, str) or not re.fullmatch(r"rclone[\w.-]*\.conf", filename):
            filename = "rclone.conf"
            self.settings.update(enabled=False, error="Conexão inválida; reconfigure a nuvem no painel")
        self.remote_path = self.root / filename
        self.backups = backups
        self.busy = False
        self.next_attempt = 0
        self.wake = asyncio.Event()

    def pending(self):
        if not self.settings.get("enabled"):
            return []
        since = self.settings["since"]
        pending = []
        for path in sorted(self.backups.root.glob("*.tar.gz")):
            try:
                if (not path.is_symlink() and path.stat().st_mtime >= since
                        and path.name not in self.receipts
                        and path.with_suffix(path.suffix + ".sha256").is_file()):
                    pending.append(path.name)
            except FileNotFoundError:
                # Local retention runs in a thread and may remove older files.
                continue
        return pending

    def describe(self):
        return {"enabled": self.settings.get("enabled", False),
                "configured": self.remote_path.is_file(),
                "provider": PROVIDERS.get(self.settings.get("provider"), ""),
                "folder": self.settings.get("folder", "Valheim/backups"),
                "pending": len(self.pending()), "uploading": self.busy,
                "last_success": self.settings.get("last_success"),
                "last_file": self.settings.get("last_file"),
                "error": self.settings.get("error")}

    def configure(self, enabled, folder, config=""):
        if self.busy:
            raise ValueError("Aguarde o envio em andamento antes de alterar a conexão")
        folder = folder.strip()
        path = PurePosixPath(folder)
        if (not folder or path.is_absolute() or any(p in (".", "..", "") for p in folder.split("/"))
                or any(c in folder for c in "\\:\r\n\0") or any(ord(c) < 32 for c in folder)):
            raise ValueError("Informe uma pasta relativa, como Valheim/backups")
        settings = self.settings | {"enabled": enabled, "folder": folder, "error": None}
        replacement = None
        if config.strip():
            provider, content = connection(config)
            fd, temporary = tempfile.mkstemp(dir=self.root, prefix="rclone-", suffix=".conf")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as file:
                    file.write(content)
                    file.flush()
                    os.fsync(file.fileno())
            except BaseException:
                Path(temporary).unlink(missing_ok=True)
                raise
            replacement = Path(temporary)
            settings["provider"] = provider
            settings["connection"] = replacement.name
        if enabled and replacement is None and not self.remote_path.is_file():
            raise ValueError("Importe a conexão antes de ativar o envio")
        if enabled:
            settings.setdefault("since", time.time())
        try:
            atomic_json(self.config_path, settings)
        except BaseException:
            if replacement:
                replacement.unlink(missing_ok=True)
            raise
        previous = self.remote_path
        self.settings = settings
        if replacement:
            self.remote_path = replacement
            try:
                previous.unlink(missing_ok=True)
            except OSError:
                pass  # An obsolete credential file must not invalidate the saved connection.
        self.next_attempt = 0
        self.wake.set()
        return self.describe()

    async def copy(self, source, target):
        # Never inherit RCLONE_* overrides or expose provider errors/tokens to logs.
        env = {k: v for k, v in os.environ.items() if k in {"PATH", "HOME", "SystemRoot", "SSL_CERT_FILE", "SSL_CERT_DIR"}}
        child = await asyncio.create_subprocess_exec(
            "rclone", "copyto", str(source), target, "--config", str(self.remote_path),
            "--retries", "2", "--low-level-retries", "2", "--contimeout", "15s",
            "--timeout", "60s", "--stats", "0", "--log-level", "ERROR",
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL, env=env)
        try:
            code = await asyncio.wait_for(child.wait(), timeout=300)
        except BaseException:
            if child.returncode is None:
                child.kill()
            await child.wait()
            raise
        if code:
            raise RuntimeError("Envio falhou. Verifique a conexão, as permissões e o espaço na nuvem")

    async def upload_one(self):
        pending = self.pending()
        if self.busy or not pending or time.monotonic() < self.next_attempt:
            return False
        self.busy = True
        name = pending[0]
        try:
            source = self.backups.resolve(name)
            checksum = source.with_suffix(source.suffix + ".sha256")
            if checksum.is_symlink() or await asyncio.to_thread(digest, source) != checksum.read_text().strip():
                raise RuntimeError("Checksum local inválido; exportação suspensa para este arquivo")
            target = "cloud:" + self.settings["folder"] + "/" + name
            await self.copy(source, target)
            await self.copy(checksum, target + ".sha256")
            now = time.time()
            receipts = self.receipts | {name: {"time": now, "provider": self.settings["provider"], "folder": self.settings["folder"]}}
            atomic_json(self.receipts_path, receipts)
            self.receipts = receipts
            self.settings.update(last_success=now, last_file=name, error=None)
            atomic_json(self.config_path, self.settings)
            return True
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Only our fixed messages are safe; subprocess exceptions may contain paths.
            self.settings["error"] = str(exc) if type(exc) is RuntimeError else "Não foi possível exportar o backup; verifique a conexão e o armazenamento local"
            self.next_attempt = time.monotonic() + 300
            atomic_json(self.config_path, self.settings)
            return False
        finally:
            self.busy = False

    async def run(self):
        while True:
            self.wake.clear()
            try:
                if await self.upload_one():
                    continue
            except Exception:
                # A local persistence failure must not stop subsequent retries.
                self.next_attempt = time.monotonic() + 300
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=5)
            except asyncio.TimeoutError:
                pass
