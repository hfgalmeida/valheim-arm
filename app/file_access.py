"""Keep the installer's SSH user able to manage persistent, editable data."""

import asyncio
import json
import logging
import os
import subprocess
from pathlib import Path

log = logging.getLogger("valheim")
INTERVAL = 30


class FileAccess:
    def __init__(self, data):
        self.data = data
        self.profile = data / "config/file-access.json"
        self.cache = {}
        self.failed = False

    def uid(self):
        if not self.profile.exists():
            return None
        if not self.safe(self.profile):
            raise ValueError("Perfil de acesso inválido")
        profile = json.loads(self.profile.read_text(encoding="utf-8"))
        if not isinstance(profile, dict):
            raise ValueError("Perfil de acesso inválido")
        value = profile.get("uid")
        if type(value) is not int or not 0 < value < 2**31:
            raise ValueError("UID de acesso aos arquivos inválido")
        return value

    def safe(self, path):
        return not any(item.is_symlink() for item in (path, *path.parents))

    def sync(self):
        try:
            uid = self.uid()
            if uid is None:
                return
            entries = [(self.data, False), (self.data / "config", False)]
            overrides = self.data / "config/overrides.json"
            if overrides.exists():
                entries.append((overrides, True))
            for name in ("saves", "backups", "mods"):
                root = self.data / name
                if not self.safe(root) or not root.is_dir():
                    continue
                for base, dirs, files in os.walk(root, followlinks=False):
                    dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
                    entries.append((Path(base), True))
                    entries.extend((Path(base) / name, True) for name in files)
            current = {}
            groups = {}
            for path, editable in entries:
                if not self.safe(path) or not path.exists():
                    continue
                stat = path.stat()
                if not (path.is_dir() or path.is_file()):
                    continue
                key = (uid, stat.st_ino, stat.st_mode, stat.st_ctime_ns)
                current[path] = key
                if self.cache.get(path) == key:
                    continue
                # A host-owned private file is already accessible to that user.
                if os.getuid() != 0 and stat.st_uid != os.getuid():
                    continue
                groups.setdefault((editable, path.is_dir()), []).append(path)
            for (editable, directory), paths in groups.items():
                permission = "rwX" if editable else "r-x"
                acl = f"u:{uid}:{permission},u:10001:{permission}"
                if editable and directory:
                    acl += f",d:u:{uid}:rwx,d:u:10001:rwx"
                for offset in range(0, len(paths), 100):
                    batch = paths[offset : offset + 100]
                    subprocess.run(
                        ["setfacl", "-P", "-m", acl, "--", *map(str, batch)],
                        check=True,
                        capture_output=True,
                        timeout=20,
                    )
                    for path in batch:
                        stat = path.stat()
                        current[path] = (uid, stat.st_ino, stat.st_mode, stat.st_ctime_ns)
            self.cache = current
            self.failed = False
        except (OSError, ValueError, subprocess.SubprocessError):
            if not self.failed:
                log.warning(
                    "Acesso SSH aos arquivos não pôde ser sincronizado; confira as permissões e o pacote acl"
                )
            self.failed = True

    async def run(self):
        while True:
            await asyncio.to_thread(self.sync)
            await asyncio.sleep(INTERVAL)


def prepare(data, uid):
    if os.getuid() != 0 or not 0 < uid < 2**31:
        raise ValueError("Preparação de acesso exige root e um UID de usuário não root")
    access = FileAccess(data)
    if not access.safe(data):
        raise ValueError("A pasta data não pode ser um link simbólico")
    for name in ("config", "saves", "backups", "mods"):
        path = data / name
        if not access.safe(path):
            raise ValueError("Links simbólicos não são aceitos nos diretórios de dados")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chown(path, 10001, 10001)
    from .config import atomic_json

    atomic_json(access.profile, {"uid": uid})
    os.chown(access.profile, 10001, 10001)
    access.sync()
    if access.failed:
        raise RuntimeError("Não foi possível preparar o acesso aos arquivos")
