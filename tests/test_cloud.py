import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.cloud import Cloud, connection


def remote(provider="drive"):
    return '[my-account]\ntype = ' + provider + '\ntoken = {"access_token":"secret-access","refresh_token":"secret-refresh"}\n' + ('drive_id = personal-drive\ndrive_type = personal\n' if provider == "onedrive" else '')


def archive(runtime):
    (runtime.data / "saves/world.db").write_bytes(b"world")
    return runtime.backups.create("test", runtime.config.effective())


@pytest.mark.parametrize("provider", ["drive", "onedrive", "dropbox"])
def test_import_supported_connection(provider):
    name, config = connection(remote(provider))
    assert name == provider
    assert "[cloud]" in config
    assert "secret-refresh" in config


@pytest.mark.parametrize("config", ["nonsense", "[a]\ntype=local", remote() + "\n[b]\ntype=drive",
    remote() + "\ntoken_command=echo secret", remote() + "\nclient_credentials=true",
    "[a]\ntype=drive\ntoken={}", "[DEFAULT]\nfoo=bar\n" + remote()])
def test_reject_unsafe_connections(config):
    with pytest.raises(ValueError):
        connection(config)


@pytest.mark.parametrize("folder", ["/absolute", "../escape", "a/../b", "remote:path", "a\\b", "a\nb", "a//b"])
def test_reject_unsafe_destinations(runtime, folder):
    with pytest.raises(ValueError):
        runtime.cloud.configure(True, folder, remote())


def test_upload_and_durable_receipt_without_secrets(runtime):
    old = archive(runtime)
    c = runtime.cloud
    c.configure(True, "Valheim/meu mundo", remote())
    assert old not in c.pending()
    name = archive(runtime)
    c.copy = AsyncMock()
    assert asyncio.run(c.upload_one())
    assert c.copy.await_count == 2
    assert c.copy.await_args_list[0].args == (runtime.backups.resolve(name), "cloud:Valheim/meu mundo/" + name)
    assert c.copy.await_args_list[1].args[1].endswith(".tar.gz.sha256")
    reloaded = Cloud(runtime.data, runtime.backups)
    assert reloaded.pending() == []
    assert reloaded.describe()["last_file"] == name
    assert "secret-" not in json.dumps(reloaded.describe())
    manifest, _ = runtime.backups.validate(runtime.backups.resolve(name))
    assert not any(path.startswith("cloud/") for path in manifest["files"])
    if os.name != "nt":
        assert c.remote_path.stat().st_mode & 0o777 == 0o600
        assert c.root.stat().st_mode & 0o777 == 0o700


def test_failed_upload_survives_restart_and_does_not_delete_backup(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    name = archive(runtime)
    c.copy = AsyncMock(side_effect=OSError("secret-access"))
    assert not asyncio.run(c.upload_one())
    assert c.pending() == [name]
    assert "secret-access" not in json.dumps(c.describe())
    assert not asyncio.run(c.upload_one())
    assert c.copy.await_count == 1
    reloaded = Cloud(runtime.data, runtime.backups)
    reloaded.copy = AsyncMock()
    assert asyncio.run(reloaded.upload_one())
    assert runtime.backups.resolve(name).exists()


def test_checksum_upload_failure_is_retried(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    name = archive(runtime)
    c.copy = AsyncMock(side_effect=[None, RuntimeError("Envio falhou")])
    assert not asyncio.run(c.upload_one())
    assert c.pending() == [name]
    assert name not in c.receipts


def test_checksum_corruption_blocks_export(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    name = archive(runtime)
    runtime.backups.resolve(name).write_bytes(b"tampered")
    c.copy = AsyncMock()
    assert not asyncio.run(c.upload_one())
    c.copy.assert_not_awaited()
    assert "Checksum" in c.describe()["error"]


def test_disabled_and_config_changes_during_upload(runtime):
    c = runtime.cloud
    with pytest.raises(ValueError):
        c.configure(True, "Valheim")
    c.configure(True, "Valheim", remote())
    c.busy = True
    with pytest.raises(ValueError, match="andamento"):
        c.configure(False, "other")
    c.busy = False
    c.configure(False, "Valheim")
    archive(runtime)
    c.copy = AsyncMock()
    assert not asyncio.run(c.upload_one())
    c.copy.assert_not_awaited()


def test_connection_replacement_is_atomic_and_preserves_refreshed_token(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    previous = c.remote_path
    refreshed = previous.read_text().replace("secret-access", "new-access")
    previous.write_text(refreshed)
    c.configure(True, "other")
    assert previous.read_text() == refreshed
    with patch("app.cloud.atomic_json", side_effect=OSError("disk full")):
        with pytest.raises(OSError):
            c.configure(True, "new-folder", remote("dropbox"))
    reloaded = Cloud(runtime.data, runtime.backups)
    assert reloaded.remote_path == previous
    assert reloaded.remote_path.read_text() == refreshed
    assert reloaded.describe()["folder"] == "other"
    assert len(list(c.root.glob("*.conf"))) == 1
    c.configure(True, "new-folder", remote("dropbox"))
    assert not previous.exists()
    assert Cloud(runtime.data, runtime.backups).describe()["provider"] == "Dropbox"


def test_invalid_cloud_state_does_not_prevent_game_runtime_startup(runtime):
    runtime.cloud.config_path.write_text("broken JSON")
    c = Cloud(runtime.data, runtime.backups)
    assert not c.describe()["enabled"]
    assert c.describe()["error"]
    c.configure(True, "Valheim", remote())
    assert c.describe()["enabled"]


def test_pending_files_are_protected_from_retention(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    names = [archive(runtime) for _ in range(4)]
    runtime.backups.prune(1, 1, set(c.pending()))
    assert len(runtime.backups.list()) == 4
    c.copy = AsyncMock()
    for _ in names:
        assert asyncio.run(c.upload_one())
    runtime.backups.prune(1, 1, set(c.pending()))
    assert len(runtime.backups.list()) == 1


def test_snapshot_schedules_export_without_waiting_for_network(runtime):
    runtime.cloud.configure(True, "Valheim", remote())
    runtime.cloud.copy = AsyncMock(side_effect=RuntimeError("offline"))
    name = asyncio.run(runtime.snapshot("manual"))
    assert name in runtime.cloud.pending()
    runtime.cloud.copy.assert_not_awaited()
    assert runtime.cloud.wake.is_set()


def test_copy_uses_fixed_arguments_and_no_environment_overrides(runtime, monkeypatch):
    c = runtime.cloud
    monkeypatch.setenv("RCLONE_CONFIG_CLOUD_TYPE", "local")
    child = SimpleNamespace(wait=AsyncMock(return_value=0), returncode=0)
    with patch("app.cloud.asyncio.create_subprocess_exec", AsyncMock(return_value=child)) as spawn:
        asyncio.run(c.copy(c.root / "file", "cloud:folder/file"))
    args, kwargs = spawn.call_args
    assert args[:2] == ("rclone", "copyto")
    assert str(c.remote_path) in args
    assert not any(key.startswith("RCLONE_") for key in kwargs["env"])


def test_cancel_upload_kills_child_and_keeps_pending(runtime):
    c = runtime.cloud
    c.configure(True, "Valheim", remote())
    name = archive(runtime)
    from unittest.mock import Mock
    child = SimpleNamespace(wait=AsyncMock(side_effect=[asyncio.CancelledError(), 0]), returncode=None, kill=Mock())
    with patch("app.cloud.asyncio.create_subprocess_exec", AsyncMock(return_value=child)):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(c.upload_one())
    child.kill.assert_called_once()
    assert not c.busy
    assert c.pending() == [name]
