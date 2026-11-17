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
        pytest.skip("Symbolic links unavailable")
    with pytest.raises(ValueError):
        upload(plugins, "test.dll", dll(2))
    assert outside.read_bytes() == dll()


@pytest.mark.parametrize("name", ["example.dll", "Author-Plugin/nested/example.dll"])
def test_delete_and_restore_preserve_bytes_and_configs(tmp_path, name):
    plugins = Plugins(tmp_path)
    active = plugins.root / name
    active.parent.mkdir(parents=True)
    active.write_bytes(dll())
    config = tmp_path / "mods/BepInEx/config/example.cfg"
    config.parent.mkdir(parents=True)
    config.write_text("private configuration")
    revision = plugins.info(active)["revision"]
    removed = plugins.delete(name, revision)
    assert not active.exists()
    assert plugins.list() == []
    assert plugins.list_deleted()[0]["name"] == name
    assert config.read_text() == "private configuration"
    restored = plugins.restore_deleted(removed["id"], revision)
    assert restored["name"] == name
    assert active.read_bytes() == dll()
    assert not plugins.list_deleted()
    assert config.read_text() == "private configuration"


def test_deletion_conflict_and_failed_archive_leave_active_dll_intact(tmp_path):
    import os

    plugins = Plugins(tmp_path)
    original = upload(plugins, "example.dll", dll())
    with pytest.raises(Conflict):
        plugins.delete("example.dll", "f" * 64)
    replace = os.replace

    def fail_move(source, target):
        if source == plugins.root / "example.dll":
            raise OSError("move Failed")
        return replace(source, target)

    with patch("app.plugins.os.replace", side_effect=fail_move):
        with pytest.raises(OSError):
            plugins.delete("example.dll", original["revision"])
    assert (plugins.root / "example.dll").read_bytes() == dll()
    assert not plugins.list_deleted()
    assert not list(plugins.trash.iterdir())


def test_restoration_refuses_changed_copy_or_existing_plugin(tmp_path):
    plugins = Plugins(tmp_path)
    original = upload(plugins, "example.dll", dll())
    removed = plugins.delete("example.dll", original["revision"])
    upload(plugins, "example.dll", dll(2))
    with pytest.raises(Conflict):
        plugins.restore_deleted(removed["id"], original["revision"])
    assert (plugins.root / "example.dll").read_bytes() == dll(2)
    payload = plugins.trash / removed["id"] / "plugin.dll"
    payload.write_bytes(dll(3))
    with pytest.raises(Conflict):
        plugins.restore_deleted(removed["id"], original["revision"])
    assert (plugins.root / "example.dll").read_bytes() == dll(2)


@pytest.mark.parametrize(
    "name",
    [
        "../core/library.dll",
        "/tmp/private.dll",
        "sub/../../x.dll",
        "a\\x.dll",
        "C:/x.dll",
        "./x.dll",
        "sub//x.dll",
        "example.cfg",
    ],
)
def test_plugin_delete_never_escapes_plugin_tree(tmp_path, name):
    with pytest.raises(ValueError):
        Plugins(tmp_path).delete(name, "a" * 64)


def test_multiple_deleted_versions_are_preserved(tmp_path):
    plugins = Plugins(tmp_path)
    a = upload(plugins, "example.dll", dll())
    first = plugins.delete("example.dll", a["revision"])
    b = upload(plugins, "example.dll", dll(2))
    second = plugins.delete("example.dll", b["revision"])
    assert first["id"] != second["id"]
    assert len(plugins.list_deleted()) == 2
    plugins.restore_deleted(first["id"], a["revision"])
    assert (plugins.root / "example.dll").read_bytes() == dll()
    assert plugins.list_deleted()[0]["id"] == second["id"]


def test_restoration_never_overwrites_a_concurrent_upload(tmp_path):
    plugins = Plugins(tmp_path)
    original = upload(plugins, "example.dll", dll())
    removed = plugins.delete("example.dll", original["revision"])

    def concurrent(source, target, **kwargs):
        target.write_bytes(dll(2))
        raise FileExistsError("concurrent upload")

    with patch("app.plugins.os.link", side_effect=concurrent):
        with pytest.raises(Conflict):
            plugins.restore_deleted(removed["id"], original["revision"])
    assert (plugins.root / "example.dll").read_bytes() == dll(2)
    assert len(plugins.list_deleted()) == 1


def test_delete_refuses_symlinked_parent_and_keeps_external_dll(tmp_path):
    plugins = Plugins(tmp_path)
    plugins.root.mkdir(parents=True)
    external = tmp_path / "outside"
    external.mkdir()
    (external / "example.dll").write_bytes(dll())
    try:
        (plugins.root / "linked").symlink_to(external, target_is_directory=True)
    except OSError:
        pytest.skip("Symbolic links unavailable")
    with pytest.raises(ValueError):
        plugins.delete("linked/example.dll", "a" * 64)
    assert (external / "example.dll").read_bytes() == dll()
