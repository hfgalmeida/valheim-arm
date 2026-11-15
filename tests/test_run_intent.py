import asyncio
import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.run_intent import RunIntent
from app.runtime import Runtime
from tests.test_web import client_fixture


def test_missing_state_preserves_legacy_autostart(tmp_path):
    state = RunIntent(tmp_path)
    assert state.running and not state.error
    assert not state.path.exists()


def test_stop_is_saved_when_accepted_and_survives_recreation(runtime):
    runtime.process = SimpleNamespace(returncode=None)
    runtime.submit("stop")
    assert runtime.live and not runtime.want_running
    restored = Runtime(runtime.data, runtime.config)
    assert not restored.want_running and not restored.run_intent.running
    asyncio.run(restored.tick())
    assert restored.queue.empty()


def test_latest_start_stop_request_wins_and_stale_job_cannot_overwrite(runtime):
    first = runtime.submit("start")
    stop = runtime.submit("stop")
    last = runtime.submit("start")
    assert first["state"] == stop["state"] == "cancelado"
    assert runtime.submit("start") is last
    runtime.start = AsyncMock()

    async def scenario():
        await runtime.execute(stop)
        assert runtime.run_intent.running
        await runtime.execute(last)

    asyncio.run(scenario())
    runtime.start.assert_awaited_once()
    assert RunIntent(runtime.data).running


def test_shutdown_does_not_store_manual_stop(runtime):
    runtime.set_run_intent(True)
    before = runtime.run_intent.path.read_bytes()
    asyncio.run(runtime.shutdown())
    assert not runtime.want_running
    assert runtime.run_intent.path.read_bytes() == before
    assert Runtime(runtime.data, runtime.config).want_running


def test_recovery_suspension_does_not_change_administrator_preference(runtime):
    runtime.set_run_intent(True)
    runtime.startup_failures = 4
    runtime.recovery_failed()
    assert not runtime.want_running and runtime.recovery_error
    assert RunIntent(runtime.data).running
    assert Runtime(runtime.data, runtime.config).want_running


@pytest.mark.parametrize(
    "bad", [{}, True, {"version": 1, "running": "false"}, {"version": 2, "running": True}]
)
def test_invalid_file_keeps_game_stopped_but_can_be_repaired(runtime, bad):
    runtime.run_intent.path.write_text(json.dumps(bad))
    restored = Runtime(runtime.data, runtime.config)
    assert not restored.want_running
    assert restored.status()["run_intent"]["error"]
    assert restored.diagnostics()["phase"] == "failed"
    restored.submit("start")
    assert RunIntent(runtime.data).running
    assert not restored.run_intent.error


def test_failed_save_rejects_action_without_queueing_or_changing_running_state(
    runtime, monkeypatch
):
    client = client_fixture(runtime, monkeypatch)
    with patch("app.run_intent.atomic_json", side_effect=OSError("disk full")):
        response = client.post("/api/v1/actions", json={"action": "stop"})
    assert response.status_code == 409
    assert "Operação não aceita" in response.json()["detail"]
    assert runtime.want_running and runtime.run_intent.running
    assert runtime.queue.empty()


def test_full_queue_does_not_change_preference(runtime):
    runtime.queue = asyncio.Queue(maxsize=1)
    runtime.queue.put_nowait({})
    with pytest.raises(asyncio.QueueFull):
        runtime.submit("stop")
    assert runtime.run_intent.running
    assert not runtime.run_intent.path.exists()


def test_stopped_boot_discards_old_automatic_maintenance(runtime):
    runtime.schedule_once("restart")
    runtime.schedule_once("start")
    runtime.run_intent.set(False)
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.empty()
    assert all(job["state"] == "cancelado" for job in restored.jobs)


def test_manual_stop_during_backup_prevents_its_finally_from_starting_game(runtime):
    runtime.process = SimpleNamespace(returncode=None)

    async def stop(**kwargs):
        runtime.process.returncode = 0

    async def backup(*args):
        runtime.submit("stop")
        return "backup.tar.gz"

    runtime.stop = AsyncMock(side_effect=stop)
    runtime.snapshot = AsyncMock(side_effect=backup)
    runtime.start = AsyncMock()
    asyncio.run(runtime.execute({"action": "backup"}))
    runtime.start.assert_not_awaited()
    assert not RunIntent(runtime.data).running


def test_maintenance_of_stopped_game_never_changes_preference(runtime):
    runtime.set_run_intent(False)
    before = runtime.run_intent.path.read_bytes()
    runtime.stop = AsyncMock()
    runtime.snapshot = AsyncMock(return_value="backup.tar.gz")
    runtime.start = AsyncMock()
    asyncio.run(runtime.execute({"action": "backup"}))
    runtime.start.assert_not_awaited()
    assert runtime.run_intent.path.read_bytes() == before
    runtime.install = AsyncMock()
    asyncio.run(runtime.execute({"action": "update", "scheduled": True}))
    runtime.install.assert_not_awaited()


def test_stop_arriving_during_install_prevents_process_launch(runtime):
    install = runtime.data / "installed"
    install.mkdir()
    installed = False

    def current():
        return install if installed else None

    async def prepare():
        nonlocal installed
        installed = True
        runtime.submit("stop")

    runtime.current_install = current
    runtime.install = AsyncMock(side_effect=prepare)
    with (
        patch("app.runtime.install_bepinex"),
        patch("app.runtime.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
    ):
        asyncio.run(runtime.start())
    spawn.assert_not_awaited()
    assert runtime.state == "parado" and runtime.clean_stop
    assert not runtime.clean_marker.exists()


@pytest.mark.parametrize("running", [True, False])
def test_boot_respects_saved_intent_without_network_or_game(runtime, running):
    async def scenario():
        runtime.run_intent.set(running)
        fresh = Runtime(runtime.data, runtime.config)
        sleeper = asyncio.Event()
        fresh.worker = AsyncMock(side_effect=sleeper.wait)
        fresh.scheduler = AsyncMock(side_effect=sleeper.wait)

        async def pause(*args):
            await sleeper.wait()

        for helper in (fresh.cloud, fresh.alerts, fresh.file_access, fresh.image_version):
            helper.run = AsyncMock(side_effect=pause)
        fake_fcntl = SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=Mock())
        with patch.dict(sys.modules, {"fcntl": fake_fcntl}), patch("app.runtime.preflight"):
            await fresh.boot()
            assert fresh.queue.qsize() == int(running)
            await fresh.shutdown()
        assert RunIntent(fresh.data).running is running

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["backup", "restart", "update"])
def test_running_maintenance_preserves_saved_revision(runtime, action):
    runtime.set_run_intent(True)
    before = runtime.run_intent.path.read_bytes()
    runtime.process = SimpleNamespace(returncode=None)

    async def stopped(**kwargs):
        runtime.process.returncode = 0

    runtime.stop = AsyncMock(side_effect=stopped)
    runtime.snapshot = AsyncMock(return_value="backup.tar.gz")
    runtime.install = AsyncMock()
    runtime.start = AsyncMock()
    asyncio.run(runtime.execute({"action": action}))
    runtime.start.assert_awaited_once()
    assert runtime.run_intent.path.read_bytes() == before


def test_stop_during_last_preparation_does_not_write_active_marker_or_spawn(runtime):
    install = runtime.data / "installed"
    install.mkdir()
    runtime.current_install = lambda: install

    async def thread(function, *args):
        if function == runtime.mod_diagnostics.begin:
            runtime.submit("stop")
            return
        return function(*args)

    with (
        patch("app.runtime.asyncio.to_thread", side_effect=thread),
        patch("app.runtime.asyncio.create_subprocess_exec", new_callable=AsyncMock) as spawn,
    ):
        asyncio.run(runtime.start())
    spawn.assert_not_awaited()
    assert runtime.clean_stop and not runtime.clean_marker.exists()
