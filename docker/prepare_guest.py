"""Extract signed Ubuntu amd64 packages using native ARM apt/dpkg tools.

No guest maintainer script or second emulator is needed during the build.
"""

import hashlib
import json
import os
import struct
import subprocess
import tempfile
from email.parser import Parser
from pathlib import Path


PACKAGES = ("libatomic1", "libstdc++6", "libpulse0", "libpulse-mainloop-glib0")
LIBRARIES = (
    "libatomic.so.1",
    "libstdc++.so.6",
    "libpulse.so.0",
    "libpulse-simple.so.0",
    "libpulse-mainloop-glib.so.0",
)


def check_elf(path):
    with path.open("rb") as file:
        header = file.read(20)
    if (
        len(header) < 20
        or header[:6] != b"\x7fELF\x02\x01"
        or struct.unpack("<H", header[18:20])[0] != 62
    ):
        raise ValueError(f"Biblioteca amd64 ausente ou de arquitetura incorreta: {path}")


def apt_config(work):
    for name in ("state/lists/partial", "cache/archives/partial", "empty"):
        (work / name).mkdir(parents=True, exist_ok=True)
    (work / "status").touch()
    sources = work / "sources.list"
    key = "/usr/share/keyrings/ubuntu-archive-keyring.gpg"
    sources.write_text(
        "\n".join(
            f"deb [arch=amd64 signed-by={key}] {url} {suite} main universe"
            for url, suite in (
                ("https://archive.ubuntu.com/ubuntu", "noble"),
                ("https://archive.ubuntu.com/ubuntu", "noble-updates"),
                ("https://security.ubuntu.com/ubuntu", "noble-security"),
            )
        )
        + "\n"
    )
    config = work / "apt.conf"
    # APT_CONFIG is loaded first, so host apt hooks and ports.ubuntu.com sources
    # are excluded, as is the ARM package status database.
    config.write_text(
        f"""APT::Architecture "amd64";
APT::Architectures {{ "amd64"; }};
Dir::Etc::main "{work / 'empty/main'}";
Dir::Etc::parts "{work / 'empty'}";
Dir::Etc::sourcelist "{sources}";
Dir::Etc::sourceparts "{work / 'empty'}";
Dir::State "{work / 'state'}";
Dir::State::status "{work / 'status'}";
Dir::Cache "{work / 'cache'}";
Dir::Cache::archives "{work / 'cache/archives'}";
Dir::Log "{work / 'log'}";
Acquire::Languages "none";
"""
    )
    return config


def package_metadata(path):
    fields = Parser().parsestr(
        subprocess.check_output(["dpkg-deb", "--field", str(path)], text=True)
    )
    if (
        fields["Architecture"] not in ("amd64", "all")
        or not fields["Package"]
        or not fields["Version"]
    ):
        raise ValueError(f"Pacote de arquitetura inesperada: {path.name}")
    with path.open("rb") as file:
        checksum = hashlib.file_digest(file, "sha256").hexdigest()
    return {
        "name": fields["Package"],
        "version": fields["Version"],
        "architecture": fields["Architecture"],
        "sha256": checksum,
    }


def validate(root):
    loader = root / "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    check_elf(loader)
    env = os.environ | {"FEX_ROOTFS": str(root)}
    # ldd uses LD_TRACE_LOADED_OBJECTS, which may trace native FEX instead of the
    # guest library. Invoke the x86 loader explicitly with --list instead.
    env.pop("LD_TRACE_LOADED_OBJECTS", None)
    directories = (
        root / "usr/lib/x86_64-linux-gnu",
        root / "lib/x86_64-linux-gnu",
        root / "usr/lib/x86_64-linux-gnu/pulseaudio",
    )
    for name in LIBRARIES:
        library = directories[0] / name
        check_elf(library)
        subprocess.run(
            [
                "FEXInterpreter",
                str(loader),
                "--library-path",
                ":".join(map(str, directories)),
                "--list",
                str(library),
            ],
            env=env,
            check=True,
            timeout=30,
        )


def prepare(root, info_path):
    with tempfile.TemporaryDirectory(prefix="valheim-amd64-") as temporary:
        work = Path(temporary)
        env = os.environ | {"APT_CONFIG": str(apt_config(work))}
        subprocess.run(["apt-get", "update"], env=env, check=True)
        subprocess.run(
            [
                "apt-get",
                "install",
                "--download-only",
                "--yes",
                "--no-install-recommends",
                *PACKAGES,
            ],
            env=env,
            check=True,
        )
        archives = sorted((work / "cache/archives").glob("*.deb"))
        if not archives:
            raise ValueError("APT não baixou as bibliotecas amd64")
        metadata = [package_metadata(path) for path in archives]
        for path in archives:
            subprocess.run(["dpkg-deb", "--extract", str(path), str(root)], check=True)
    validate(root)
    info = json.loads(info_path.read_text())
    info["guest_packages"] = metadata
    info["guest_libraries_checked"] = list(LIBRARIES)
    info_path.write_text(json.dumps(info, indent=2))


if __name__ == "__main__":
    prepare(Path("/opt/rootfs"), Path("/opt/build-info.json"))
