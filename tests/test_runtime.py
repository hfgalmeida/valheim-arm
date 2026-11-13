import asyncio
import signal
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.runtime import Runtime, public_build


def test_public_branch_not_first():
    output = 'noise "896660" { "depots" { "branches" { "beta" { "buildid" "999" } "public" { "buildid" "123" } } } } noise'
    assert public_build(output) == "123"
    with pytest.raises(RuntimeError):
        public_build('"896660" { "depots" {} }')


def test_operations_require_empty_and_query(runtime):
    runtime.process = SimpleNamespace(pid=42, returncode=None)
    runtime.running = runtime.config.effective()
    with pytest.raises(RuntimeError, match="zero jogadores"):
        asyncio.run(runtime.stop())
    runtime.players.consume("Connections 0 ZDOS:1")
    runtime.players.empty_since -= 121
    with patch("app.runtime.query_steam", return_value=None):
        with pytest.raises(RuntimeError, match="Steam"):
            asyncio.run(runtime.stop())


def test_final_connection_cancels_and_resumes(runtime):
    runtime.process = SimpleNamespace(pid=42, returncode=None)
    runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
    runtime.players.consume("Connections 0 ZDOS:1")
    runtime.players.empty_since -= 121

    async def connection(_):
        runtime.players.consume("Got connection SteamID 123")

    with patch("app.runtime.os.killpg", create=True) as kill, patch("app.runtime.asyncio.sleep", side_effect=connection), \
            patch("app.runtime.signal.SIGSTOP", 19, create=True), patch("app.runtime.signal.SIGCONT", 18, create=True):
        with pytest.raises(RuntimeError, match="verificação final"):
            asyncio.run(runtime.stop())
        assert [call.args[1] for call in kill.call_args_list] == [19, 18]


def test_failed_update_restarts_previous_install(runtime):
    runtime.process = SimpleNamespace(returncode=None)

    async def stop():
        runtime.process.returncode = 0

    runtime.stop = AsyncMock(side_effect=stop)
    runtime.snapshot = AsyncMock(return_value="safe-backup")
    runtime.install = AsyncMock(side_effect=RuntimeError("download failed"))
    runtime.start = AsyncMock()
    with pytest.raises(RuntimeError, match="download failed"):
        asyncio.run(runtime.execute({"action": "update"}))
    runtime.start.assert_awaited_once()


def test_stop_timeout_never_runs_backup(runtime):
    runtime.process = SimpleNamespace(returncode=None)
    runtime.stop = AsyncMock(side_effect=RuntimeError("timeout"))
    runtime.snapshot = AsyncMock()
    runtime.install = AsyncMock()
    with pytest.raises(RuntimeError):
        asyncio.run(runtime.execute({"action": "update"}))
    runtime.snapshot.assert_not_awaited()
    runtime.install.assert_not_awaited()


def test_coalescing_and_persistent_pending(runtime):
    runtime.schedule_once("backup")
    runtime.schedule_once("restart")
    runtime.schedule_once("update")
    assert runtime.queue.qsize() == 1
    assert runtime.jobs[0]["action"] == "update"
    runtime.jobs[0]["state"] = "adiado"
    runtime.persist_pending()
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.qsize() == 1
    assert restored.jobs[0]["action"] == "update"


def test_clean_stop_required_for_snapshot(runtime):
    runtime.clean_stop = False
    with pytest.raises(RuntimeError, match="limpo"):
        asyncio.run(runtime.snapshot("backup"))


def test_worker_serializes_and_continues_after_failure(runtime):
    async def scenario():
        active, maximum, order = 0, 0, []

        async def execute(job):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            order.append(job["action"])
            await asyncio.sleep(0)
            active -= 1
            if job["action"] == "backup":
                raise RuntimeError("disk full")

        runtime.execute = execute
        runtime.submit("backup")
        runtime.submit("restart")
        task = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert maximum == 1 and order == ["backup", "restart"]
        assert [j["state"] for j in runtime.jobs] == ["falha", "concluído"]

    asyncio.run(scenario())


def test_failed_initial_install_is_retried_without_busy_loop(runtime):
    async def scenario():
        runtime.start = AsyncMock(side_effect=RuntimeError("Steam offline"))
        runtime.submit("start")
        worker = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        worker.cancel()
        await asyncio.gather(worker, return_exceptions=True)
        assert runtime.next_recovery > time.monotonic()
        await runtime.tick()
        assert runtime.queue.empty()
        runtime.next_recovery = 0
        await runtime.tick()
        assert runtime.queue.qsize() == 1
        assert runtime.jobs[-1]["action"] == "start"
        await runtime.tick()
        assert runtime.queue.qsize() == 1

    asyncio.run(scenario())


def test_manual_stop_disables_recovery_even_after_crash(runtime):
    async def scenario():
        runtime.clean_stop = False
        await runtime.execute({"action": "stop"})
        await runtime.tick()
        assert not runtime.want_running
        assert runtime.queue.empty()
        runtime.start = AsyncMock()
        await runtime.execute({"action": "start"})
        assert runtime.want_running
        runtime.start.assert_awaited_once()

    asyncio.run(scenario())


def test_crash_recovery_uses_existing_world_not_restore(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(returncode=1)
        runtime.clean_stop = False
        runtime.start = AsyncMock()
        runtime.backups.restore = lambda *_: (_ for _ in ()).throw(AssertionError("must not restore saves automatically"))
        await runtime.tick()
        job = runtime.jobs[-1]
        assert job["action"] == "start"
        await runtime.execute(job)
        runtime.start.assert_awaited_once()

    asyncio.run(scenario())


def test_shutdown_never_schedules_recovery(runtime):
    runtime.closing = True
    asyncio.run(runtime.tick())
    assert runtime.queue.empty()


def test_scheduled_failure_is_persisted_and_retried(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(returncode=None)
        runtime.execute = AsyncMock(side_effect=RuntimeError("occupied"))
        runtime.schedule_once("restart")
        task = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert runtime.jobs[0]["state"] == "adiado"
        assert runtime.next_retry > time.monotonic()
        runtime.next_retry = 0
        await runtime.tick()
        assert runtime.jobs[0]["state"] == "fila"

    asyncio.run(scenario())


def test_shutdown_does_not_restart_during_snapshot_failure(runtime):
    runtime.process = SimpleNamespace(returncode=None)

    async def stop():
        runtime.process.returncode = 0

    runtime.stop = AsyncMock(side_effect=stop)
    runtime.snapshot = AsyncMock(side_effect=ValueError("disk full"))
    runtime.start = AsyncMock()
    runtime.closing = True
    with pytest.raises(ValueError):
        asyncio.run(runtime.execute({"action": "backup"}))
    runtime.start.assert_not_awaited()


def test_restore_does_not_prune_selected_source(runtime):
    runtime.backups.create = lambda *args: "backup-name"
    runtime.backups.prune = lambda *args: (_ for _ in ()).throw(AssertionError("must not prune"))
    assert asyncio.run(runtime.snapshot("pre-restore")) == "backup-name"


def test_persistence_disk_full_does_not_kill_worker(runtime):
    async def scenario():
        runtime.schedule_once("restart")
        runtime.execute = AsyncMock()
        with patch("app.runtime.atomic_json", side_effect=OSError("disk full")):
            task = asyncio.create_task(runtime.worker())
            await runtime.queue.join()
            assert not task.done()
            assert runtime.jobs[0]["state"] == "adiado"
            runtime.execute.assert_not_awaited()
            assert runtime.persistence_error
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())


@pytest.mark.parametrize("saved", [False, True])
def test_stop_requires_save_after_signal(runtime, saved):
    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
        runtime.clean_stop = False
        runtime.clean_marker.write_text("{}")
        runtime.players.consume("Connections 0 ZDOS:1")
        runtime.players.empty_since -= 121

        async def finish():
            if saved:
                runtime.last_save = time.monotonic()
            runtime.process.returncode = 0

        runtime.reader = finish()
        with patch("app.runtime.os.killpg", create=True) as kill, patch("app.runtime.asyncio.sleep", new=AsyncMock()), \
                patch("app.runtime.signal.SIGSTOP", 19, create=True), patch("app.runtime.signal.SIGCONT", 18, create=True):
            if saved:
                await runtime.stop()
                assert runtime.clean_stop and not runtime.clean_marker.exists()
            else:
                with pytest.raises(RuntimeError, match="World saved"):
                    await runtime.stop()
                assert not runtime.clean_stop and runtime.clean_marker.exists()
            assert [call.args[1] for call in kill.call_args_list] == [19, signal.SIGINT, 18]

    asyncio.run(scenario())
