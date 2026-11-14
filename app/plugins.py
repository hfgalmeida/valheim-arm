"""Bounded, atomic uploads of managed DLLs into BepInEx/plugins."""

import hashlib
import os
import re
import struct
import tempfile
from contextlib import contextmanager
from pathlib import Path

from .mod_configs import Conflict

MAX_PLUGIN_SIZE = 16 * 1024 * 1024


def managed_dll(data):
    """Check PE/CLR structure, without loading or executing the assembly."""
    try:
        if data[:2] != b"MZ":
            return False
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if pe < 64 or data[pe : pe + 4] != b"PE\x00\x00":
            return False
        machine, sections = struct.unpack_from("<HH", data, pe + 4)
        size, flags = struct.unpack_from("<HH", data, pe + 20)
        optional = pe + 24
        magic = struct.unpack_from("<H", data, optional)[0]
        base = {0x10B: 96, 0x20B: 112}.get(magic)
        if machine not in (0x14C, 0x8664) or not flags & 0x2000 or base is None:
            return False
        if (
            not 0 < sections <= 96
            or size < base + 15 * 8
            or optional + size + sections * 40 > len(data)
        ):
            return False
        if struct.unpack_from("<I", data, optional + base - 4)[0] < 15:
            return False
        rva, length = struct.unpack_from("<II", data, optional + base + 14 * 8)
        if not rva or length < 72:
            return False
        # Locate the CLR header in raw section data, rather than trusting its RVA.
        for index in range(sections):
            _, start, raw_size, raw = struct.unpack_from(
                "<IIII", data, optional + size + index * 40 + 8
            )
            if start <= rva and rva - start + 72 <= raw_size:
                offset = raw + rva - start
                return (
                    raw + raw_size <= len(data)
                    and offset + 72 <= len(data)
                    and struct.unpack_from("<I", data, offset)[0] >= 72
                )
    except (struct.error, OverflowError):
        pass
    return False


class Plugins:
    def __init__(self, data):
        self.root = data / "mods/BepInEx/plugins"
        self.backups = data / "mods/plugin-backups"
        self.incoming = data / "mods/plugin-staging"

    def safe(self, path):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Links simbólicos não são permitidos nos plugins")

    def target(self, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.dll", name, re.IGNORECASE):
            raise ValueError("Envie uma DLL com nome simples, sem pastas ou caracteres especiais")
        path = self.root / name
        self.safe(path)
        if self.root.is_dir() and any(
            item.name.casefold() == name.casefold() and item.name != name
            for item in self.root.iterdir()
        ):
            raise ValueError(
                "Já existe uma DLL com esse nome em outra capitalização; use o nome exato"
            )
        if path.exists() and not path.is_file():
            raise ValueError("O destino do plugin não é um arquivo")
        return path

    def info(self, path):
        size = path.stat().st_size
        revision = (
            hashlib.sha256(path.read_bytes()).hexdigest() if size <= MAX_PLUGIN_SIZE else None
        )
        previous = self.backups / (path.name + ".previous")
        self.safe(previous)
        return {
            "name": path.relative_to(self.root).as_posix(),
            "bytes": size,
            "revision": revision,
            "previous_available": path.parent == self.root and previous.is_file(),
        }

    def list(self):
        self.safe(self.root)
        if not self.root.exists():
            return []
        # Show nested plugins too, but uploads intentionally target only this folder.
        result = []
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            for name in files:
                path = Path(base) / name
                if path.suffix.lower() == ".dll" and not path.is_symlink() and path.is_file():
                    result.append(self.info(path))
        return sorted(result, key=lambda item: item["name"])

    @contextmanager
    def staging(self, name):
        self.target(name)
        self.root.mkdir(parents=True, exist_ok=True)
        self.safe(self.incoming)
        self.incoming.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.incoming, prefix=".plugin-upload-", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "wb") as file:
                yield file, Path(temporary)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def commit(self, name, temporary, revision=None):
        target = self.target(name)
        data = temporary.read_bytes()
        if not data or len(data) > MAX_PLUGIN_SIZE or not managed_dll(data):
            raise ValueError(
                "Envie uma DLL .NET válida, de até 16 MiB, compatível com BepInEx/Mono"
            )
        if target.exists():
            current = self.info(target)["revision"]
            if current is None:
                raise ValueError("A DLL existente ultrapassa 16 MiB; gerencie-a pelo SSH")
            if current != revision:
                raise Conflict(
                    "A DLL já existe ou mudou. Atualize a lista e confirme a substituição"
                )
            backup = self.backups / (name + ".previous")
            self.safe(backup)
            self.backups.mkdir(parents=True, exist_ok=True)
            # A failed backup must leave the active DLL untouched.
            descriptor, old = tempfile.mkstemp(dir=self.backups, prefix=".plugin-backup-")
            try:
                with os.fdopen(descriptor, "wb") as file:
                    file.write(target.read_bytes())
                    file.flush()
                    os.fsync(file.fileno())
                os.replace(old, backup)
            finally:
                Path(old).unlink(missing_ok=True)
        elif revision is not None:
            raise Conflict("A DLL mudou desde a confirmação; atualize a lista")
        os.replace(temporary, target)
        return self.info(target)

    def restore(self, name, revision):
        self.target(name)
        backup = self.backups / (name + ".previous")
        self.safe(backup)
        if not backup.is_file() or backup.stat().st_size > MAX_PLUGIN_SIZE:
            raise ValueError("Não há versão anterior disponível para este plugin")
        with self.staging(name) as (file, temporary):
            file.write(backup.read_bytes())
            file.flush()
            os.fsync(file.fileno())
            file.close()
            return self.commit(name, temporary, revision)
