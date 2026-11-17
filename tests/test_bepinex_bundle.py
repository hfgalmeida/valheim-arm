import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import zipfile

import pytest

from docker.bundle_bepinex import bundle
from app.runtime import install_bepinex


def fixture_pack(tmp_path, extra=None):
    archive = tmp_path / "upstream.zip"
    header = bytearray(64)
    header[:6] = b"\x7fELF\x02\x01"
    header[18:20] = b"\x3e\x00"
    files = {
        "doorstop_libs/libdoorstop_x64.so": bytes(header),
        "BepInEx/core/BepInEx.dll": b"core",
        "BepInEx/core/BepInEx.Preloader.dll": b"preloader",
        "BepInEx/config/BepInEx.cfg": b"[Logging.Console]\nEnabled = true\n[Preloader.Entrypoint]\nType = GameObject\n",
        "winhttp.dll": b"windows-must-not-ship",
        "doorstop_libs/libdoorstop_x64.dylib": b"macos-must-not-ship",
    }
    with zipfile.ZipFile(archive, "w") as output:
        for name, content in files.items():
            output.writestr("BepInExPack_Valheim/" + name, content)
        if extra:
            entry = zipfile.ZipInfo()
            entry.filename = extra
            output.writestr(entry, b"unsafe")
    license_path = tmp_path / "LICENSE"
    license_path.write_text("MIT test license")
    settings = {
        "version": "5.4.2360",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "url": "https://example.com/pack.zip",
    }
    return archive, settings, license_path


def make_bundle(tmp_path):
    archive, settings, license_path = fixture_pack(tmp_path)
    destination = tmp_path / "bundle"
    bundle(archive, destination, settings, license_path)
    return destination


def test_build_keeps_linux_core_config_and_license_with_verified_hash(tmp_path):
    destination = make_bundle(tmp_path)
    metadata = json.loads((destination / "metadata.json").read_text())
    assert metadata["sha256"] == hashlib.sha256((destination / "pack.zip").read_bytes()).hexdigest()
    assert (destination / "LICENSE").read_text() == "MIT test license"
    with zipfile.ZipFile(destination / "pack.zip") as archive:
        assert set(archive.namelist()) == {
            "libdoorstop.so",
            "BepInEx/core/BepInEx.dll",
            "BepInEx/core/BepInEx.Preloader.dll",
            "BepInEx/config/BepInEx.cfg",
        }


def test_bundle_is_deterministic_and_rejects_changed_download(tmp_path):
    archive, settings, license_path = fixture_pack(tmp_path)
    first, second = tmp_path / "first", tmp_path / "second"
    bundle(archive, first, settings, license_path)
    bundle(archive, second, settings, license_path)
    assert (first / "pack.zip").read_bytes() == (second / "pack.zip").read_bytes()
    archive.write_bytes(b"modified-download")
    with pytest.raises(ValueError, match="SHA256"):
        bundle(archive, second, settings, license_path)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "C:/outside", "folder\\outside"])
def test_build_rejects_unsafe_paths(tmp_path, name):
    archive, settings, license_path = fixture_pack(tmp_path, name)
    with pytest.raises(ValueError, match="Unsafe path"):
        bundle(archive, tmp_path / "bundle", settings, license_path)


def test_default_install_is_offline_and_preserves_plugins_config_patchers(tmp_path):
    destination = make_bundle(tmp_path)
    data, install = tmp_path / "data", tmp_path / "install"
    data.mkdir()
    install.mkdir()
    settings = SimpleNamespace(bepinex=True, bepinex_url="", bepinex_sha256="")
    with (
        patch("app.runtime.BEPINEX_BUNDLE", destination),
        patch("app.runtime.download", side_effect=AssertionError("network forbidden")),
        patch.object(Path, "symlink_to"),
    ):
        install_bepinex(data, install, settings)
        saved = data / "mods/BepInEx/config/BepInEx.cfg"
        assert "Type = GameObject" in saved.read_text()
        saved.write_text("custom-user-config")
        plugin = data / "mods/BepInEx/plugins/plugin.dll"
        patcher = data / "mods/BepInEx/patchers/patcher.dll"
        plugin.write_bytes(b"user-plugin")
        patcher.write_bytes(b"user-patcher")
        stale = data / "mods/BepInEx/core/stale.dll"
        stale.write_bytes(b"old-core")
        # Force reinstallation to model a new bundled version or repaired core.
        (data / "mods/pack.json").write_text("{}")
        install_bepinex(data, install, settings)
        assert not stale.exists()
        assert saved.read_text() == "custom-user-config"
        assert plugin.read_bytes() == b"user-plugin"
        assert patcher.read_bytes() == b"user-patcher"
        assert json.loads((data / "mods/pack.json").read_text())["source"] == "bundled"


def test_disabled_does_not_read_or_install_bundle(tmp_path):
    settings = SimpleNamespace(bepinex=False)
    with patch("app.runtime.BEPINEX_BUNDLE", tmp_path / "missing"):
        install_bepinex(tmp_path, tmp_path, settings)
    assert not (tmp_path / "mods").exists()


def test_corrupted_bundled_archive_is_not_installed(tmp_path):
    destination = make_bundle(tmp_path)
    (destination / "pack.zip").write_bytes(b"corrupted")
    settings = SimpleNamespace(bepinex=True, bepinex_url="", bepinex_sha256="")
    with patch("app.runtime.BEPINEX_BUNDLE", destination):
        with pytest.raises(ValueError, match="SHA256"):
            install_bepinex(tmp_path, tmp_path, settings)
    assert not (tmp_path / "mods/pack.json").exists()


def test_custom_upstream_layout_still_supported(tmp_path):
    archive, metadata, _ = fixture_pack(tmp_path)
    data, install = tmp_path / "data", tmp_path / "install"
    data.mkdir()
    install.mkdir()
    settings = SimpleNamespace(
        bepinex=True, bepinex_url=metadata["url"], bepinex_sha256=metadata["sha256"]
    )

    def download(url, path):
        path.write_bytes(archive.read_bytes())

    with patch("app.runtime.download", side_effect=download), patch.object(Path, "symlink_to"):
        install_bepinex(data, install, settings)
    assert (data / "mods/libdoorstop.so").read_bytes().startswith(b"\x7fELF")
    assert json.loads((data / "mods/pack.json").read_text())["source"] == "custom"
