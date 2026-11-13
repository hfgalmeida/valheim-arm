import hashlib
import io
import json
import os
import shutil
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


def digest(path):
    with path.open("rb") as file:
        return hashlib.file_digest(file, "sha256").hexdigest()


class Backups:
    def __init__(self, data):
        self.data = data
        self.root = data / "backups"
        self.root.mkdir(parents=True, exist_ok=True)

    def resolve(self, name):
        if Path(name).name != name or not name.endswith(".tar.gz"):
            raise ValueError("Nome de backup inválido")
        path = self.root / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Backup não encontrado")
        return path

    def list(self):
        return [{"name": p.name, "bytes": p.stat().st_size} for p in sorted(self.root.glob("*.tar.gz"), reverse=True)]

    def create(self, reason, settings, build="unknown"):
        saves = self.data / "saves"
        if not saves.exists():
            raise ValueError("Não há saves para arquivar")
        files = []
        for base in (saves, self.data / "config", self.data / "mods/BepInEx/config"):
            if base.exists():
                for path in base.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("Links simbólicos não são aceitos nos dados de backup")
                    if path.is_file() and path.name != "admin.json":
                        files.append(path)
        size = sum(p.stat().st_size for p in files)
        if shutil.disk_usage(self.root).free < size * 2 + 64 * 1024**2:
            raise ValueError("Espaço insuficiente para criar e validar o backup")
        now = datetime.now(timezone.utc)
        name = now.strftime("%Y%m%dT%H%M%S%fZ") + ".tar.gz"
        temp = self.root / (name + ".partial")
        manifest = {"format": 1, "created": now.isoformat(), "reason": reason,
                    "world": settings.world_name, "build": build,
                    "files": {p.relative_to(self.data).as_posix(): digest(p) for p in files}}
        try:
            with tarfile.open(temp, "w:gz", compresslevel=1) as archive:
                for path in files:
                    archive.add(path, arcname=path.relative_to(self.data).as_posix(), recursive=False)
                content = json.dumps(manifest).encode()
                info = tarfile.TarInfo("manifest.json")
                info.size, info.mode = len(content), 0o600
                archive.addfile(info, io.BytesIO(content))
            self.validate(temp)
            target = self.root / name
            checksum = digest(temp)
            temp.replace(target)
            target.with_suffix(target.suffix + ".sha256").write_text(checksum + "\n", encoding="ascii")
            return name
        finally:
            temp.unlink(missing_ok=True)

    def validate(self, path):
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            names = set()
            total = 0
            for member in members:
                name = PurePosixPath(member.name)
                if (not member.isfile() or name.is_absolute() or ".." in name.parts or "\\" in member.name
                        or member.name in names or member.size < 0):
                    raise ValueError("Arquivo de backup contém entrada insegura")
                if member.name != "manifest.json" and not (member.name.startswith("saves/") or member.name.startswith("config/") or member.name.startswith("mods/BepInEx/config/")):
                    raise ValueError("Arquivo fora dos diretórios permitidos")
                if member.name == "config/admin.json":
                    raise ValueError("Credencial administrativa não pode ser restaurada pelo painel")
                names.add(member.name)
                total += member.size
                if total > 100 * 1024**3 or len(names) > 100000:
                    raise ValueError("Backup excede limites de extração")
            manifest_info = archive.getmember("manifest.json")
            if manifest_info.size > 8 * 1024**2:
                raise ValueError("Manifesto excessivo")
            manifest = json.load(archive.extractfile(manifest_info))
            if manifest.get("format") != 1 or set(manifest["files"]) != names - {"manifest.json"}:
                raise ValueError("Manifesto inválido")
            for name, checksum in manifest["files"].items():
                with archive.extractfile(name) as file:
                    if hashlib.file_digest(file, "sha256").hexdigest() != checksum:
                        raise ValueError("Checksum inválido no backup")
            return manifest, total

    def restore(self, name):
        path = self.resolve(name)
        checksum = path.with_suffix(path.suffix + ".sha256")
        if not checksum.exists() or digest(path) != checksum.read_text().strip():
            raise ValueError("Checksum do arquivo de backup inválido ou ausente")
        manifest, total = self.validate(path)
        if shutil.disk_usage(self.data).free < total + 64 * 1024**2:
            raise ValueError("Espaço insuficiente para restaurar")
        # Restore only save/list files. Config is archived for recovery but must be
        # reapplied explicitly, avoiding silently changing credentials or schedules.
        with tempfile.TemporaryDirectory(dir=self.data, prefix="restore-") as tmp:
            stage = Path(tmp) / "saves"
            stage.mkdir()
            with tarfile.open(path, "r:gz") as archive:
                for member in archive.getmembers():
                    if member.name.startswith("saves/"):
                        destination = Path(tmp) / member.name
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        with archive.extractfile(member) as source, destination.open("wb") as target:
                            shutil.copyfileobj(source, target)
            current = self.data / "saves"
            previous = Path(tmp) / "previous"
            if current.exists():
                current.rename(previous)
            try:
                stage.rename(current)
            except BaseException:
                if previous.exists():
                    previous.rename(current)
                raise
        return manifest

    def prune(self, daily, weekly, protected=()):
        groups, keep = set(), set()
        valid = []
        for item in self.list():
            path = self.resolve(item["name"])
            sidecar = path.with_suffix(path.suffix + ".sha256")
            if not sidecar.exists():
                continue
            valid.append(path)
        for path in valid:
            day = path.name[:8]
            if day not in groups and len(groups) < daily:
                groups.add(day)
                keep.add(path)
        groups = set()
        for path in valid:
            week = datetime.strptime(path.name[:8], "%Y%m%d").isocalendar()[:2]
            if week not in groups and len(groups) < weekly:
                groups.add(week)
                keep.add(path)
        # Keep latest in addition to the daily/weekly representatives.
        if valid:
            keep.add(valid[0])
        for path in valid:
            if path not in keep and path.name not in protected:
                path.unlink()
                path.with_suffix(path.suffix + ".sha256").unlink(missing_ok=True)
