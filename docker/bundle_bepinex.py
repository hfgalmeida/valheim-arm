"""Build a verified, Linux x86-64-only BepInEx bundle; no assemblies are executed."""

import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import sys
import tempfile
import urllib.request
import zipfile


MAX_ARCHIVE = 32 * 1024**2


def bundle(archive, output, settings, license_path):
    if hashlib.sha256(archive.read_bytes()).hexdigest() != settings["sha256"]:
        raise ValueError("SHA256 do pacote BepInEx não confere")
    with zipfile.ZipFile(archive) as pack:
        entries = pack.infolist()
        if sum(item.file_size for item in entries) > 128 * 1024**2:
            raise ValueError("Pacote BepInEx excessivo")
        if len({item.filename for item in entries}) != len(entries):
            raise ValueError("Entradas duplicadas no pacote")
        for item in entries:
            path = PurePosixPath(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or any(c in item.orig_filename for c in "\\:\x00")
                or (item.external_attr >> 16) & 0o170000 == 0o120000
            ):
                raise ValueError("Caminho inseguro no pacote BepInEx")
        prefix = "BepInExPack_Valheim/"
        library = pack.read(prefix + "doorstop_libs/libdoorstop_x64.so")
        if (
            library[:5] != b"\x7fELF\x02"
            or library[5:6] != b"\x01"
            or library[18:20] != b"\x3e\x00"
        ):
            raise ValueError("Doorstop deve ser ELF64 Linux x86-64")
        for required in (
            "BepInEx/core/BepInEx.dll",
            "BepInEx/core/BepInEx.Preloader.dll",
            "BepInEx/config/BepInEx.cfg",
        ):
            pack.getinfo(prefix + required)
        output.mkdir(parents=True, exist_ok=True)
        target = output / "pack.zip"
        with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as normalized:

            def write(name, payload):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                normalized.writestr(info, payload)

            write("libdoorstop.so", library)
            for item in entries:
                name = item.filename.removeprefix(prefix)
                if (
                    not item.is_dir()
                    and item.filename.startswith(prefix)
                    and (name.startswith("BepInEx/core/") or name == "BepInEx/config/BepInEx.cfg")
                ):
                    write(name, pack.read(item))
        metadata = settings | {
            "upstream_sha256": settings["sha256"],
            "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }
        (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
        shutil.copy2(license_path, output / "LICENSE")


def main():
    settings = json.loads(Path(sys.argv[1]).read_text())
    output, license_path = Path(sys.argv[2]), Path(sys.argv[3])
    with tempfile.TemporaryDirectory() as temporary:
        archive = Path(temporary) / "package.zip"
        request = urllib.request.Request(
            settings["url"], headers={"User-Agent": "ValheimARM-build"}
        )
        with urllib.request.urlopen(request, timeout=60) as source:
            payload = source.read(MAX_ARCHIVE + 1)
        if len(payload) > MAX_ARCHIVE:
            raise ValueError("Download BepInEx excessivo")
        archive.write_bytes(payload)
        bundle(archive, output, settings, license_path)


if __name__ == "__main__":
    main()
