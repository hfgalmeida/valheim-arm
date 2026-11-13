import hashlib
import json
import shutil
from pathlib import Path

home = Path("/tmp/fex-home")
candidates = [p for p in home.rglob("*") if p.is_dir() and
              (p / "usr/lib/x86_64-linux-gnu/libc.so.6").exists()]
if len(candidates) != 1:
    raise SystemExit(f"Rootfs extraído não identificado: {candidates}")
root = candidates[0]
if not any((root / p).exists() or (root / p).is_symlink() for p in (
        "lib/ld-linux.so.2", "lib32/ld-linux.so.2", "usr/lib32/ld-linux.so.2",
        "lib/i386-linux-gnu/ld-linux.so.2", "usr/lib/i386-linux-gnu/ld-linux.so.2")):
    raise SystemExit("Rootfs não inclui loader i386 necessário ao SteamCMD")
shutil.move(str(root), "/opt/rootfs")
info_path = Path("/opt/build-info.json")
info = json.loads(info_path.read_text())
images = [p for p in home.rglob("*") if p.suffix in (".sqsh", ".ero")]
info["rootfs"] = "Ubuntu 24.04, FEXRootFSFetcher (hash upstream verificado pelo fetcher)"
info["rootfs_images"] = {p.name: hashlib.file_digest(p.open("rb"), "sha256").hexdigest() for p in images}
info_path.write_text(json.dumps(info, indent=2))
