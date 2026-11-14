import hashlib
import json
import runpy
import re
from pathlib import Path

import pytest

collect = runpy.run_path(str(Path(__file__).resolve().parents[1] / "docker/collect_rootfs.py"))[
    "collect_rootfs"
]


def test_docker_data_override_matches_fex_rootfs_path_concatenation():
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text(encoding="utf-8")
    data = re.search(r"FEX_APP_DATA_LOCATION=([^\s]+)", dockerfile)[1]
    # FEX-2611.1 uses GetDataDirectory() + "RootFS/", without adding a separator.
    assert Path(data + "RootFS/") == Path(data) / "RootFS"
    assert dockerfile.index('mkdir -p "$HOME" "$FEX_APP_DATA_LOCATION"') < dockerfile.index(
        "&& FEXRootFSFetcher"
    )


def root_fixture(data, name="Ubuntu_24_04", with_i386=True):
    root = data / "RootFS" / name
    libc = root / "usr/lib/x86_64-linux-gnu/libc.so.6"
    libc.parent.mkdir(parents=True)
    libc.write_bytes(b"libc-fixture")
    if with_i386:
        loader = root / "usr/lib/i386-linux-gnu/ld-linux.so.2"
        loader.parent.mkdir(parents=True)
        loader.write_bytes(b"i386-loader-fixture")
    return root


def test_collect_uses_explicit_fex_data_location(tmp_path):
    data = tmp_path / "custom-data"
    source = root_fixture(data)
    image = data / "RootFS/Ubuntu_24_04.sqsh"
    image.write_bytes(b"image-fixture")
    info = tmp_path / "build-info.json"
    info.write_text('{"fex_version":"FEX-2611.1"}')
    destination = tmp_path / "rootfs"
    collect(data, destination, info)
    assert (destination / "usr/lib/x86_64-linux-gnu/libc.so.6").read_bytes() == b"libc-fixture"
    assert not source.exists()
    metadata = json.loads(info.read_text())
    assert metadata["fex_version"] == "FEX-2611.1"
    assert metadata["rootfs_directory"] == "Ubuntu_24_04"
    assert metadata["rootfs_images"][image.name] == hashlib.sha256(b"image-fixture").hexdigest()


def test_missing_download_directory_has_actionable_error(tmp_path):
    with pytest.raises(ValueError, match="FEX_APP_DATA_LOCATION"):
        collect(tmp_path / "missing", tmp_path / "out", tmp_path / "info.json")


def test_archive_without_extracted_rootfs_is_rejected(tmp_path):
    root = tmp_path / "RootFS"
    root.mkdir()
    (root / "Ubuntu_24_04.sqsh").write_bytes(b"image-fixture")
    with pytest.raises(ValueError, match="Ubuntu_24_04.sqsh"):
        collect(tmp_path, tmp_path / "out", tmp_path / "info.json")


def test_i386_loader_required_before_moving_files(tmp_path):
    source = root_fixture(tmp_path, with_i386=False)
    with pytest.raises(ValueError, match="loader i386"):
        collect(tmp_path, tmp_path / "out", tmp_path / "info.json")
    assert source.exists()
    assert not (tmp_path / "out").exists()


def test_ambiguous_rootfs_is_rejected(tmp_path):
    root_fixture(tmp_path, "first")
    root_fixture(tmp_path, "second")
    with pytest.raises(ValueError, match="Esperado um rootfs"):
        collect(tmp_path, tmp_path / "out", tmp_path / "info.json")


def test_existing_destination_is_never_overwritten(tmp_path):
    source = root_fixture(tmp_path)
    destination = tmp_path / "out"
    destination.mkdir()
    with pytest.raises(ValueError, match="já existe"):
        collect(tmp_path, destination, tmp_path / "info.json")
    assert source.exists()
