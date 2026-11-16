import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

spec = importlib.util.spec_from_file_location(
    "host_update", Path(__file__).resolve().parents[1] / "update-server.py"
)
host_update = importlib.util.module_from_spec(spec)
spec.loader.exec_module(host_update)


class FakeUpdater(host_update.Updater):
    def __init__(self, root):
        super().__init__(root)
        self.commands = []
        self.failure = None
        self.same = False
        self.readiness = 0

    def run(self, *args, capture=False):
        self.commands.append(args)
        if args == ("compose", "ps", "--quiet", "valheim"):
            return "container-id"
        if args[:1] == ("inspect",):
            return json.dumps([{"Image": "sha256:old", "Config": {"Env": ["WORLD_NAME=MyWorld"]}}])
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": "sha256:old" if self.same else "sha256:new"}])
        if self.failure == "preflight" and args[-1] == "preflight":
            raise RuntimeError("preflight failed")
        if self.failure == "backup" and args[-1] == host_update.BACKUP_CODE:
            raise RuntimeError("backup failed")
        return ""

    def wait_ready(self):
        self.readiness += 1
        if self.failure == "startup" and self.readiness == 1:
            raise RuntimeError("new image failed")


@pytest.fixture
def updater(tmp_path):
    (tmp_path / ".env").write_text(
        'SERVER_PASSWORD="do-not-change"\nVALHEIM_IMAGE=original:latest\n'
    )
    (tmp_path / "data").mkdir()
    with patch.object(
        host_update.shutil, "disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)
    ):
        yield FakeUpdater(tmp_path)


def test_update_orders_backup_before_switch_and_pins_image(updater):
    updater.update("public/image:latest")
    commands = updater.commands
    stop = commands.index(("compose", "stop", "valheim"))
    backup = next(i for i, command in enumerate(commands) if command[-1] == host_update.BACKUP_CODE)
    start = commands.index(("compose", "up", "-d", "--no-build", "--pull", "never"))
    assert stop < backup < start
    assert commands.index(("pull", "--platform", "linux/arm64", "public/image:latest")) < stop
    assert "WORLD_NAME=MyWorld" in commands[backup]
    assert "VALHEIM_IMAGE=valheim-arm-local:current-new" in (updater.root / ".env").read_text()
    assert 'SERVER_PASSWORD="do-not-change"' in (updater.root / ".env").read_text()
    assert json.loads(updater.state.read_text())["phase"] == "completed"


def test_same_image_does_not_stop_game(updater):
    updater.same = True
    updater.update("public/image:latest")
    assert not any(command[:2] == ("compose", "stop") for command in updater.commands)


def test_failed_preflight_does_not_stop_game(updater):
    updater.failure = "preflight"
    with pytest.raises(RuntimeError):
        updater.update("public/image:latest")
    assert not any(command[:2] == ("compose", "stop") for command in updater.commands)


def test_backup_failure_restarts_old_container_without_switch(updater):
    updater.failure = "backup"
    with pytest.raises(RuntimeError, match="Backup"):
        updater.update("public/image:latest")
    assert ("compose", "start", "valheim") in updater.commands
    assert "VALHEIM_IMAGE=original:latest" in (updater.root / ".env").read_text()
    assert not any(command[:2] == ("compose", "up") for command in updater.commands)


def test_new_image_failure_rolls_back_and_preserves_settings(updater):
    updater.failure = "startup"
    with pytest.raises(RuntimeError, match="anterior restaurada"):
        updater.update("public/image:latest")
    assert "VALHEIM_IMAGE=valheim-arm-local:previous-old" in (updater.root / ".env").read_text()
    assert updater.readiness == 2
    assert json.loads(updater.state.read_text())["phase"] == "rolled-back"


def test_manual_rollback(updater):
    updater.update("public/image:latest")
    updater.update("ignored", rollback=True)
    assert "VALHEIM_IMAGE=valheim-arm-local:previous-old" in (updater.root / ".env").read_text()


def test_interrupted_update_requires_recovery(updater):
    updater.save({"phase": "switching"})
    with pytest.raises(RuntimeError, match="rollback"):
        updater.update("public/image:latest")
    assert not updater.commands


def test_env_update_removes_duplicate_image_assignments_without_executing(tmp_path):
    path = tmp_path / ".env"
    path.write_text(
        "# keep\nSERVER_NAME='$(do-not-run)'\nexport VALHEIM_IMAGE=one\n VALHEIM_IMAGE=two\n"
    )
    host_update.set_image(path, "local:fixed")
    assert path.read_text() == "# keep\nSERVER_NAME='$(do-not-run)'\nVALHEIM_IMAGE=local:fixed\n"


def test_ready_requires_game_online_not_just_panel(updater):
    with (
        patch.object(
            updater, "compose", return_value=json.dumps({"supervisor": "ok", "game": "iniciando"})
        ),
        patch.object(host_update.time, "monotonic", side_effect=[0, 1, 1000]),
        patch.object(host_update.time, "sleep"),
    ):
        with pytest.raises(RuntimeError, match="pronto"):
            host_update.Updater.wait_ready(updater)


def test_insufficient_disk_does_not_stop_or_pull(updater):
    with patch.object(host_update.shutil, "disk_usage", return_value=SimpleNamespace(free=1024)):
        with pytest.raises(RuntimeError, match="6 GiB"):
            updater.update("public/image:latest")
    assert not any(
        command[0] == "pull" or command[:2] == ("compose", "stop") for command in updater.commands
    )


@pytest.mark.skipif(os.name != "posix", reason="Bloqueio de arquivo Linux necessário")
def test_offline_backup_requires_clean_stop_and_exclusive_volume(tmp_path, monkeypatch):
    import fcntl
    from app.backups import Backups

    monkeypatch.setenv("SERVER_PASSWORD", "game-password")
    config = tmp_path / "config"
    config.mkdir()
    (tmp_path / "saves").mkdir()
    (tmp_path / "saves/world.db").write_bytes(b"world")
    code = host_update.BACKUP_CODE.replace("Path('/data')", "Path(" + repr(str(tmp_path)) + ")")
    marker = config / "game-active.json"
    marker.write_text("{}")
    with pytest.raises(SystemExit, match="Parada limpa"):
        exec(code, {})
    assert not list(tmp_path.glob("backups/*.tar.gz"))
    marker.unlink()
    with (config / "supervisor.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            exec(code, {})
    exec(code, {})
    backups = Backups(tmp_path)
    files = backups.list()
    assert len(files) == 1
    manifest, _ = backups.validate(backups.resolve(files[0]["name"]))
    assert "saves/world.db" in manifest["files"]


def write_stopped_intent(updater):
    path = updater.root / "data/config/run-intent.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"version": 1, "running": False}))


def test_wait_ready_accepts_healthy_panel_with_intentionally_stopped_game(updater):
    write_stopped_intent(updater)
    with (
        patch.object(
            updater,
            "compose",
            return_value=json.dumps(
                {"supervisor": "ok", "game": "parado", "desired_running": False}
            ),
        ) as compose,
        patch.object(host_update.time, "monotonic", side_effect=[0, 1, 2, 35]),
    ):
        host_update.Updater.wait_ready(updater)
    assert any("proxy" in call.args for call in compose.call_args_list)


def test_old_image_rollback_is_refused_before_stopping_when_intent_is_stopped(updater):
    write_stopped_intent(updater)
    original = updater.run

    def run(*args, **kwargs):
        if args[0] == "run" and args[-2] == "-c":
            raise RuntimeError("old image lacks intent support")
        return original(*args, **kwargs)

    with patch.object(updater, "run", side_effect=run):
        with pytest.raises(RuntimeError, match="não suporta"):
            updater.restore({"previous": "old:image"})
    assert not any(command[:2] == ("compose", "stop") for command in updater.commands)


def test_unverified_rollback_is_distinguished_and_blocks_new_updates(updater):
    updater.wait_ready = lambda: (_ for _ in ()).throw(RuntimeError("not ready"))
    state = {"previous": "old:image", "target": "new:image", "phase": "switching"}
    with pytest.raises(host_update.UpdateFailure) as error:
        updater.restore(state)
    assert error.value.code == "rollback_unverified"
    assert json.loads(updater.state.read_text())["phase"] == "rollback-unverified"
    commands = len(updater.commands)
    with pytest.raises(host_update.UpdateFailure) as error:
        updater.update("public/image:latest")
    assert error.value.code == "recovery_required"
    assert len(updater.commands) == commands
