import json
import os
import shutil
import subprocess
from unittest.mock import patch

import pytest
from app.file_access import FileAccess


def data_tree(tmp_path):
    for name in ("config", "saves", "mods/BepInEx/config", "backups", "cloud", "home", "cache"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "config/file-access.json").write_text(json.dumps({"uid": 12345}))
    for name in (
        "config/admin.json",
        "config/overrides.json",
        "cloud/token",
        "saves/world.db",
        "mods/BepInEx/config/plugin.cfg",
    ):
        (tmp_path / name).write_text("data")
    return FileAccess(tmp_path)


def test_only_editable_paths_are_shared_and_new_files_are_seen(tmp_path):
    access = data_tree(tmp_path)
    with (
        patch("app.file_access.os.getuid", return_value=0, create=True),
        patch("app.file_access.subprocess.run") as run,
    ):
        access.sync()
        names = [str(arg) for call in run.call_args_list for arg in call.args[0]]
        assert str(tmp_path / "saves/world.db") in names
        assert str(tmp_path / "config/overrides.json") in names
        assert str(tmp_path / "config/admin.json") not in names
        assert str(tmp_path / "config/file-access.json") not in names
        assert str(tmp_path / "cloud/token") not in names
        for call in run.call_args_list:
            command = call.args[0]
            acl = command[command.index("-m") + 1]
            if str(tmp_path / "mods/BepInEx/config/plugin.cfg") in command:
                assert "u:12345:rw-" in acl
                assert "X" not in acl
            if str(tmp_path / "mods/BepInEx/config") in command:
                assert "u:12345:rwx" in acl
                assert "d:u:12345:rwx" in acl
        run.reset_mock()
        access.sync()
        run.assert_not_called()
        (tmp_path / "saves/new.db").write_text("new")
        access.sync()
        assert str(tmp_path / "saves/new.db") in run.call_args.args[0]


@pytest.mark.parametrize("profile", [[], {"uid": True}, {"uid": 0}, {"uid": "1000"}])
def test_bad_profile_does_not_break_supervisor(tmp_path, profile):
    access = data_tree(tmp_path)
    access.profile.write_text(json.dumps(profile))
    access.sync()
    assert access.failed


@pytest.mark.skipif(
    os.name != "posix" or not shutil.which("setfacl"), reason="ACL Linux real necessária"
)
def test_real_acl_reapplies_access_after_private_atomic_replacement(tmp_path):
    access = data_tree(tmp_path)
    path = tmp_path / "mods/BepInEx/config/plugin.cfg"
    path.chmod(0o600)
    access.sync()
    assert not access.failed
    assert "user:12345:rw-" in subprocess.check_output(["getfacl", "-cn", str(path)], text=True)
    temporary = path.with_name("replacement.tmp")
    temporary.write_text("changed")
    temporary.chmod(0o600)
    temporary.replace(path)
    access.sync()
    assert not access.failed
    assert "user:12345:rw-" in subprocess.check_output(["getfacl", "-cn", str(path)], text=True)
    secret = subprocess.check_output(
        ["getfacl", "-cn", str(tmp_path / "config/admin.json")], text=True
    )
    assert "user:12345:" not in secret
