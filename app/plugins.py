"""Bounded plugin uploads, recoverable deletion and safe restoration."""

import hashlib
import json
import logging
import math
import os
import re
import struct
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

from .config import atomic_json
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
        self.trash = data / "mods/plugin-trash"

    def safe(self, path):
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Symbolic links are not allowed in plugins")

    def target(self, name):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.dll", name, re.IGNORECASE):
            raise ValueError(
                "Upload a DLL with a simple filename, without directories or special characters"
            )
        path = self.root / name
        self.safe(path)
        if self.root.is_dir() and any(
            item.name.casefold() == name.casefold() and item.name != name
            for item in self.root.iterdir()
        ):
            raise ValueError(
                "A DLL with a different capitalization already exists; use the exact filename"
            )
        if path.exists() and not path.is_file():
            raise ValueError("The plugin destination is not a file")
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
            raise ValueError("Upload a valid .NET DLL up to 16 MiB, compatible with BepInEx/Mono")
        if target.exists():
            current = self.info(target)["revision"]
            if current is None:
                raise ValueError("A DLL existente ultrapassa 16 MiB; gerencie-a pelo SSH")
            if current != revision:
                raise Conflict(
                    "The DLL already exists or changed. Refresh the list and confirm replacement"
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
            raise Conflict("The DLL changed since confirmation; refresh the list")
        os.replace(temporary, target)
        return self.info(target)

    def restore(self, name, revision):
        self.target(name)
        backup = self.backups / (name + ".previous")
        self.safe(backup)
        if not backup.is_file() or backup.stat().st_size > MAX_PLUGIN_SIZE:
            raise ValueError("No previous version available for this plugin")
        with self.staging(name) as (file, temporary):
            file.write(backup.read_bytes())
            file.flush()
            os.fsync(file.fileno())
            file.close()
            return self.commit(name, temporary, revision)

    def removable_target(self, name):
        relative = PurePosixPath(name)
        if (
            not name
            or len(name) > 512
            or relative.is_absolute()
            or str(relative) != name
            or len(relative.parts) > 8
            or any(part in (".", "..") for part in relative.parts)
            or "\\" in name
            or ":" in name
            or any(ord(c) < 32 for c in name)
            or relative.suffix.lower() != ".dll"
        ):
            raise ValueError(
                "Select a DLL inside the plugins folder, without absolute paths or shortcuts"
            )
        path = self.root.joinpath(*relative.parts)
        self.safe(path)
        for part in (path, *path.parents):
            if part == self.root:
                break
            if part.parent.is_dir() and any(
                item.name.casefold() == part.name.casefold() and item.name != part.name
                for item in part.parent.iterdir()
            ):
                raise Conflict("The path capitalization changed; refresh the plugin list")
        if path.exists() and not path.is_file():
            raise ValueError("The plugin is not a regular file")
        return path

    def deleted_info(self, entry_id):
        if not re.fullmatch(r"[a-f0-9]{32}", entry_id):
            raise ValueError("Invalid deleted plugin identifier")
        entry = self.trash / entry_id
        metadata, payload = entry / "metadata.json", entry / "plugin.dll"
        self.safe(metadata)
        self.safe(payload)
        if (
            not metadata.is_file()
            or metadata.stat().st_size > 4096
            or not payload.is_file()
            or payload.stat().st_size > MAX_PLUGIN_SIZE
        ):
            raise ValueError("Deleted plugin copy unavailable")
        record = json.loads(metadata.read_text(encoding="utf-8"))
        if (
            not isinstance(record, dict)
            or not isinstance(record.get("name"), str)
            or not isinstance(record.get("revision"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", record["revision"])
            or not isinstance(record.get("deleted_at"), (int, float))
            or not math.isfinite(record["deleted_at"])
        ):
            raise ValueError("Invalid deleted plugin record")
        self.removable_target(record["name"])
        revision = hashlib.sha256(payload.read_bytes()).hexdigest()
        if revision != record["revision"]:
            raise Conflict("The deleted plugin copy changed; check the files on the host")
        return {
            "id": entry_id,
            "name": record["name"],
            "revision": revision,
            "deleted_at": record["deleted_at"],
            "bytes": payload.stat().st_size,
        }

    def list_deleted(self):
        self.safe(self.trash)
        if not self.trash.exists():
            return []
        result = []
        for entry in self.trash.iterdir():
            if (
                not re.fullmatch(r"[a-f0-9]{32}", entry.name)
                or entry.is_symlink()
                or not entry.is_dir()
            ):
                continue
            try:
                result.append(self.deleted_info(entry.name))
            except (OSError, ValueError, KeyError):
                logging.getLogger("valheim").warning(
                    "Deleted plugin copy unavailable; preserved for inspection on the host"
                )
        return sorted(result, key=lambda item: item["deleted_at"], reverse=True)

    def delete(self, name, revision):
        target = self.removable_target(name)
        if not target.is_file():
            raise Conflict("The plugin is no longer installed; refresh the list")
        if target.stat().st_size > MAX_PLUGIN_SIZE:
            raise ValueError("The DLL exceeds 16 MiB; manage it through the shared files")
        if hashlib.sha256(target.read_bytes()).hexdigest() != revision:
            raise Conflict("The DLL changed since confirmation; refresh the list before deleting")
        self.safe(self.trash)
        self.trash.mkdir(parents=True, exist_ok=True)
        entry_id = uuid.uuid4().hex
        entry = self.trash / entry_id
        entry.mkdir(mode=0o700)
        metadata, payload = entry / "metadata.json", entry / "plugin.dll"
        record = {"name": name, "revision": revision, "deleted_at": time.time()}
        try:
            atomic_json(metadata, record)
            self.safe(target)
            if (
                not target.is_file()
                or target.stat().st_size > MAX_PLUGIN_SIZE
                or hashlib.sha256(target.read_bytes()).hexdigest() != revision
            ):
                raise Conflict("The DLL changed during deletion; refresh the list")
            os.replace(target, payload)
        except BaseException:
            if not payload.exists():
                metadata.unlink(missing_ok=True)
                entry.rmdir()
            raise
        return {"id": entry_id, **record}

    def restore_deleted(self, entry_id, revision):
        record = self.deleted_info(entry_id)
        if record["revision"] != revision:
            raise Conflict("The deleted copy changed; refresh the list")
        target = self.removable_target(record["name"])
        if target.exists():
            raise Conflict(
                "A DLL already exists at this path; restoration never overwrites installed plugins"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        self.safe(target)
        entry = self.trash / entry_id
        payload = entry / "plugin.dll"
        # Publish the complete file atomically and never overwrite a concurrent upload.
        try:
            os.link(payload, target, follow_symlinks=False)
        except FileExistsError:
            raise Conflict("A DLL was installed at this path; restoration cancelled") from None
        try:
            payload.unlink()
            (entry / "metadata.json").unlink()
            entry.rmdir()
        except OSError:
            logging.getLogger("valheim").warning(
                "Plugin restored; remaining deleted copy preserved"
            )
        return {"name": record["name"], "revision": revision}
