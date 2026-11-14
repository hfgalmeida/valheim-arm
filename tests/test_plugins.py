from unittest.mock import patch
import pytest
from app.plugins import Plugins, managed_dll
from app.mod_configs import Conflict
from tests.plugin_fixture import dll


def upload(plugins, name, data, revision=None):
    with plugins.staging(name) as (file, temporary):
        file.write(data)
        file.flush()
        file.close()
        return plugins.commit(name, temporary, revision)


def test_upload_replace_and_restore_keep_configuration(tmp_path):
    plugins = Plugins(tmp_path)
    config = tmp_path / "mods/BepInEx/config/test.cfg"
    config.parent.mkdir(parents=True)
    config.write_text("custom settings")
    first = upload(plugins, "test.dll", dll())
    with pytest.raises(Conflict):
        upload(plugins, "test.dll", dll(2))
    second = upload(plugins, "test.dll", dll(2), first["revision"])
    assert second["previous_available"]
    assert (plugins.root / "test.dll").read_bytes() == dll(2)
    assert (plugins.backups / "test.dll.previous").read_bytes() == dll()
    restored = plugins.restore("test.dll", second["revision"])
    assert restored["revision"] == first["revision"]
    assert config.read_text() == "custom settings"
    assert not list(plugins.incoming.glob("*.tmp"))


@pytest.mark.parametrize(
    "name",
    [
        "../test.dll",
        "/tmp/test.dll",
        "sub/test.dll",
        "test.so",
        "test.dll.exe",
        "C:test.dll",
        "a\\test.dll",
    ],
)
def test_upload_never_accepts_paths_or_other_file_types(tmp_path, name):
    with pytest.raises(ValueError):
        upload(Plugins(tmp_path), name, dll())


@pytest.mark.parametrize("data", [b"", b"not a DLL", b"MZ" + b"x" * 1000])
def test_rejects_invalid_dll_without_installing(tmp_path, data):
    plugins = Plugins(tmp_path)
    with pytest.raises(ValueError):
        upload(plugins, "test.dll", data)
    assert not (plugins.root / "test.dll").exists()
    assert not list(plugins.incoming.glob("*.tmp"))


def test_interrupted_upload_keeps_old_dll_and_removes_temporary_file(tmp_path):
    plugins = Plugins(tmp_path)
    upload(plugins, "test.dll", dll())
    with pytest.raises(RuntimeError):
        with plugins.staging("test.dll") as (file, _):
            assert not list(plugins.root.glob("*.tmp"))
            file.write(dll(2)[:300])
            raise RuntimeError("disconnected")
    assert (plugins.root / "test.dll").read_bytes() == dll()
    assert not list(plugins.incoming.glob("*.tmp"))


def test_backup_failure_never_replaces_active_plugin(tmp_path):
    plugins = Plugins(tmp_path)
    initial = upload(plugins, "test.dll", dll())
    with patch("app.plugins.os.replace", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            upload(plugins, "test.dll", dll(2), initial["revision"])
    assert (plugins.root / "test.dll").read_bytes() == dll()


def test_managed_header_requires_clr_directory_and_dll_flag():
    data = bytearray(dll())
    assert managed_dll(data)
    data[152 + 208 : 152 + 216] = bytes(8)
    assert not managed_dll(data)


def test_symlink_target_cannot_escape_plugins(tmp_path):
    plugins = Plugins(tmp_path)
    plugins.root.mkdir(parents=True)
    outside = tmp_path / "private.dll"
    outside.write_bytes(dll())
    try:
        (plugins.root / "test.dll").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks indisponíveis")
    with pytest.raises(ValueError):
        upload(plugins, "test.dll", dll(2))
    assert outside.read_bytes() == dll()
