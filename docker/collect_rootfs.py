import hashlib
import json
import os
import shutil
from pathlib import Path


def present(path):
    # An absolute guest symlink must not be resolved against the ARM host.
    return path.exists() or path.is_symlink()


def collect_rootfs(data_dir, destination, info_path):
    rootfs_dir = data_dir / "RootFS"
    if not rootfs_dir.is_dir():
        raise ValueError(
            f"Directory {rootfs_dir} missing; check FEX_APP_DATA_LOCATION and the FEXRootFSFetcher download"
        )
    candidates = [
        p
        for p in rootfs_dir.iterdir()
        if p.is_dir()
        and any(
            present(p / library)
            for library in ("usr/lib/x86_64-linux-gnu/libc.so.6", "lib/x86_64-linux-gnu/libc.so.6")
        )
    ]
    if len(candidates) != 1:
        entries = sorted(p.name for p in rootfs_dir.iterdir())
        raise ValueError(
            f"Expected an extracted Ubuntu x86_64 rootfs in {rootfs_dir}; candidates={candidates}, entries={entries}"
        )
    root = candidates[0]
    if not any(
        present(root / p)
        for p in (
            "lib/ld-linux.so.2",
            "lib32/ld-linux.so.2",
            "usr/lib32/ld-linux.so.2",
            "lib/i386-linux-gnu/ld-linux.so.2",
            "usr/lib/i386-linux-gnu/ld-linux.so.2",
        )
    ):
        raise ValueError("Rootfs does not contain the i386 loader required by SteamCMD")
    if destination.exists():
        raise ValueError(
            f"Destination {destination} already exists; rootfs collection never overwrites it"
        )
    info = json.loads(info_path.read_text(encoding="utf-8"))
    images = [p for p in rootfs_dir.iterdir() if p.is_file() and p.suffix in (".sqsh", ".ero")]
    hashes = {}
    for path in images:
        with path.open("rb") as file:
            hashes[path.name] = hashlib.file_digest(file, "sha256").hexdigest()
    info["rootfs"] = "Ubuntu 24.04, FEXRootFSFetcher (hash upstream verificado pelo fetcher)"
    info["rootfs_directory"] = root.name
    info["rootfs_images"] = hashes
    shutil.move(str(root), str(destination))
    info_path.write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Rootfs coletado: {root} -> {destination}")


def main():
    data_dir = Path(os.environ.get("FEX_APP_DATA_LOCATION", "/tmp/fex-home/.local/share/fex-emu"))
    try:
        collect_rootfs(data_dir, Path("/opt/rootfs"), Path("/opt/build-info.json"))
    except ValueError as error:
        raise SystemExit(str(error)) from None


if __name__ == "__main__":
    main()
