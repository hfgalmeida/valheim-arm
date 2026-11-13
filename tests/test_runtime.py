import asyncio
import signal
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.runtime import Runtime, SteamError, public_build


def steam_child(code, lines=()):
    return SimpleNamespace(pid=42, returncode=code,
        stdout=SimpleNamespace(readline=AsyncMock(side_effect=[*lines, b""])),
        wait=AsyncMock(return_value=code))


def test_steam_self_update_restarts_same_command_immediately(runtime):
    children = [steam_child(42, [b"Update complete, launching...\n"]),
                steam_child(0, [b"Success! App '896660' fully installed.\n"])]
    with patch("app.runtime.install_steam"), patch("app.runtime.asyncio.create_subprocess_exec", AsyncMock(side_effect=children)) as spawn:
        output = asyncio.run(runtime.run_steam(["+login", "anonymous", "+quit"]))
    assert spawn.await_count == 2
    assert spawn.await_args_list[0] == spawn.await_args_list[1]
    command = spawn.await_args_list[0].args
    assert command[command.index("+@sSteamCmdForcePlatformType") + 1] == "linux"
    assert "Success! App '896660' fully installed." in output
    assert any("42" in line["message"] for line in runtime.logs)


def test_steam_repeated_self_update_is_bounded(runtime):
    with patch("app.runtime.install_steam"), patch("app.runtime.asyncio.create_subprocess_exec", AsyncMock(side_effect=[steam_child(42) for _ in range(4)])) as spawn:
        with pytest.raises(RuntimeError, match="42"):
            asyncio.run(runtime.run_steam(["+quit"]))
    assert spawn.await_count == 4


@pytest.mark.parametrize("code", [1, 8, -11])
def test_steam_real_failure_is_reported_without_retry(runtime, code):
    with patch("app.runtime.install_steam"), patch("app.runtime.asyncio.create_subprocess_exec", AsyncMock(return_value=steam_child(code))) as spawn:
        with pytest.raises(RuntimeError, match=f"saída {code}"):
            asyncio.run(runtime.run_steam(["+quit"]))
    assert spawn.await_count == 1


def test_steam_timeout_kills_process_after_self_update_restart(runtime):
    async def blocked():
        await asyncio.Event().wait()

    child = steam_child(None)
    child.stdout.readline = blocked
    child.wait = AsyncMock(return_value=-9)
    with patch("app.runtime.install_steam"), patch("app.runtime.asyncio.create_subprocess_exec", AsyncMock(side_effect=[steam_child(42), child])), \
            patch("app.runtime.os.killpg", create=True) as kill, patch("app.runtime.signal.SIGKILL", 9, create=True):
        with pytest.raises(asyncio.TimeoutError):
            asyncio.run(runtime.run_steam(["+quit"], timeout=0.05))
    kill.assert_called_once_with(42, 9)
    child.wait.assert_awaited_once()


def test_missing_configuration_refreshes_only_app_metadata_and_retries(runtime):
    cache = runtime.data / "steamcmd/appcache/appinfo.vdf"
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b"stale")
    world = runtime.data / "saves/world.db"
    world.write_bytes(b"world-data")
    depot = runtime.data / "steamcmd/steamapps/depot.cache"
    depot.parent.mkdir(parents=True)
    depot.write_bytes(b"downloaded-depot")
    failure = SteamError(8, "ERROR! Failed to install app '896660' (Missing configuration)")
    success = "Success! App '896660' fully installed."
    runtime.run_steam = AsyncMock(side_effect=["bootstrap ready", failure, success])
    with patch("app.runtime.asyncio.sleep", new=AsyncMock()) as sleep:
        assert asyncio.run(runtime.fetch_game(runtime.data / "installs/stage")) == success
    assert runtime.run_steam.await_args_list[0].args == (["+quit"],)
    first, second = runtime.run_steam.await_args_list[1:]
    assert first == second
    assert first.args[0].index("+app_info_update") < first.args[0].index("+app_update")
    assert not cache.exists()
    assert world.read_bytes() == b"world-data"
    assert depot.read_bytes() == b"downloaded-depot"
    sleep.assert_awaited_once_with(10)


def test_missing_configuration_retries_are_bounded(runtime):
    failure = SteamError(8, "ERROR! Failed to install app '896660' (Missing configuration)")
    runtime.run_steam = AsyncMock(side_effect=["ready", failure, failure, failure])
    with patch("app.runtime.asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(SteamError):
            asyncio.run(runtime.fetch_game(runtime.data / "installs/stage"))
    assert runtime.run_steam.await_count == 4
    assert [call.args for call in sleep.await_args_list] == [(10,), (20,)]


def test_other_steam_install_error_is_not_retried(runtime):
    runtime.run_steam = AsyncMock(side_effect=["ready", SteamError(8, "Disk write failure")])
    with patch("app.runtime.asyncio.sleep", new=AsyncMock()) as sleep:
        with pytest.raises(SteamError):
            asyncio.run(runtime.fetch_game(runtime.data / "installs/stage"))
    assert runtime.run_steam.await_count == 2
    sleep.assert_not_awaited()


def test_zero_exit_code_with_missing_configuration_is_retried(runtime):
    runtime.run_steam = AsyncMock(side_effect=["ready", "ERROR! Failed to install app '896660' (Missing configuration)", "success"])
    with patch("app.runtime.asyncio.sleep", new=AsyncMock()):
        assert asyncio.run(runtime.fetch_game(runtime.data / "installs/stage")) == "success"
    assert runtime.run_steam.await_count == 3


@pytest.mark.parametrize("crossplay", [True, False])
def test_crossplay_join_code_from_game_log(runtime, crossplay):
    runtime.running = runtime.config.effective().model_copy(update={"crossplay": crossplay})
    lines = iter([
        b'Session "World" registered with join code 012345\n',
        b'Session "World" with join code 987654 and IP 1.2.3.4:2456 is active with 0 player(s)\n',
        b'Session "World" registered with join code 1234567\n',
        b'Session "World" registered with join code \n',
        b'Unrelated build number 123456\n',
        b'',
    ])
    observed = []

    async def readline():
        observed.append(runtime.status()["join_code"])
        return next(lines)

    process = SimpleNamespace(returncode=None, stdout=SimpleNamespace(readline=readline), wait=AsyncMock(return_value=0))
    runtime.process = process
    asyncio.run(runtime.read_game(process))
    assert observed == ([None, "012345", "987654", "987654", "987654", "987654"] if crossplay else [None] * 6)
    assert runtime.join_code is None


def test_join_code_uses_running_config_and_is_hidden_when_offline(runtime):
    runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
    runtime.process = SimpleNamespace(returncode=None)
    runtime.join_code = "012345"
    assert runtime.config.effective().crossplay is False
    assert runtime.status()["crossplay"] is True
    assert runtime.status()["join_code"] == "012345"
    runtime.process.returncode = 1
    assert runtime.status()["join_code"] is None
    assert runtime.status()["crossplay"] is False


def test_join_code_cleared_before_restart_attempt(runtime):
    runtime.join_code = "012345"
    runtime.current_install = lambda: None
    runtime.install = AsyncMock(side_effect=RuntimeError("offline"))
    with pytest.raises(RuntimeError, match="offline"):
        asyncio.run(runtime.start())
    assert runtime.join_code is None


def test_join_code_cleared_on_log_failure(runtime):
    runtime.join_code = "012345"
    process = SimpleNamespace(stdout=SimpleNamespace(readline=AsyncMock(side_effect=OSError("broken pipe"))),
                              wait=AsyncMock(return_value=1))
    asyncio.run(runtime.read_game(process))
    assert runtime.join_code is None


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
