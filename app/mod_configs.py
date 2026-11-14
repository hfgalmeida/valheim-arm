"""Text-only editing of existing BepInEx configuration files."""

import hashlib
import os
import tempfile
from pathlib import Path

LIMIT = 32 * 1024
EXTENSIONS = {".cfg", ".ini", ".json", ".yaml", ".yml", ".toml", ".txt", ".xml"}


class Conflict(ValueError):
    pass


class ModConfigs:
    def __init__(self, data):
        self.root = data / "mods/BepInEx/config"

    def safe_root(self):
        if any(path.is_symlink() for path in (self.root, *self.root.parents)):
            raise ValueError("Links simbólicos não são permitidos nas configurações")

    def resolve(self, name):
        self.safe_root()
        if not name or "\\" in name or ":" in name or len(name) > 256:
            raise ValueError("Caminho de configuração inválido")
        relative = Path(name)
        if (
            relative.is_absolute()
            or ".." in relative.parts
            or relative.suffix.lower() not in EXTENSIONS
        ):
            raise ValueError("Selecione um arquivo de configuração de texto")
        path = self.root / relative
        if any(part.is_symlink() for part in (path, *path.parents)):
            raise ValueError("Links simbólicos não são permitidos")
        if not path.is_file():
            raise FileNotFoundError("Configuração não encontrada")
        return path

    def text(self, path):
        with path.open("rb") as file:
            data = file.read(LIMIT + 1)
        if len(data) > LIMIT or b"\x00" in data:
            raise ValueError("O editor aceita arquivos de texto UTF-8 de até 32 KiB")
        try:
            return data.decode("utf-8-sig"), data
        except UnicodeError as exc:
            raise ValueError("A configuração precisa estar codificada em UTF-8") from exc

    def list(self):
        self.safe_root()
        if not self.root.exists():
            return []
        result = []
        # Do not traverse symlinked directories, even when listing files.
        for base, dirs, files in os.walk(self.root, followlinks=False):
            dirs[:] = [name for name in dirs if not (Path(base) / name).is_symlink()]
            for name in files:
                path = Path(base) / name
                if path.suffix.lower() in EXTENSIONS and not path.is_symlink() and path.is_file():
                    result.append(path.relative_to(self.root).as_posix())
        return sorted(result)

    def read(self, name, previous=False):
        path = self.resolve(name)
        current, raw = self.text(path)
        backup = path.with_name(path.name + ".panel-backup")
        available = backup.is_file() and not backup.is_symlink()
        if previous:
            if not available:
                raise FileNotFoundError("Não há versão anterior deste arquivo")
            current, _ = self.text(backup)
        return {
            "path": name,
            "content": current,
            "revision": hashlib.sha256(raw).hexdigest(),
            "previous_available": available,
        }

    def write_atomic(self, path, content):
        descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=".mod-config-")
        try:
            with os.fdopen(descriptor, "wb") as file:
                file.write(content)
                file.flush()
                os.fsync(file.fileno())
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)

    def save(self, name, content, revision):
        path = self.resolve(name)
        _, original = self.text(path)
        if hashlib.sha256(original).hexdigest() != revision:
            raise Conflict("O arquivo mudou desde a abertura; atualize antes de salvar")
        encoded = content.encode("utf-8")
        if len(encoded) > LIMIT or "\x00" in content:
            raise ValueError("O editor aceita texto UTF-8 de até 32 KiB, sem caracteres nulos")
        # Persist the old bytes before replacing the active configuration.
        self.write_atomic(path.with_name(path.name + ".panel-backup"), original)
        self.write_atomic(path, encoded)
        return self.read(name)
