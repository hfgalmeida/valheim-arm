import asyncio
import os
import signal
import sys
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.runtime import MaintenanceDeferred, Runtime, SteamError, public_build, save_completed


@pytest.mark.parametrize("line,expected", [
    ("World saved", True),
    ("11/13/2026 22:45:40: World saved (480ms)", True),
    ("11/13/2026 22:45:40: World save (5/5) done. Total time [480ms]", True),
    ("World save (1/5) Cloud & Backup checks done [11ms] => Save number 1", False),
    ("World save (2/5) Chunks writing done [330ms]", False),
    ("World save (3/5) DB2 writing done [91ms]", False),
    ("World save (4/5) FWL writing done [6ms]", False),
    ("World save (5/5) failed", False),
    ("Error: World saved", False),
    ("Skipping backup. World session not long enough.", False),
    ("Processo do jogo encerrado: 0", False),
])
def test_only_completed_world_saves_are_acknowledged(line, expected):
    assert save_completed(line) is expected


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


def test_initial_crashes_retry_quickly_then_back_off(runtime):
    async def scenario():
        for expected_delay in (15, 30, 60, 300):
            runtime.process = SimpleNamespace(returncode=-11, stdout=SimpleNamespace(readline=AsyncMock(return_value=b"")),
                                              wait=AsyncMock(return_value=-11))
            before = time.monotonic()
            await runtime.read_game(runtime.process)
            delay = runtime.next_recovery - before
            assert expected_delay <= delay < expected_delay + 1
            await runtime.tick()
            assert runtime.queue.empty()
        assert runtime.startup_failures == 4

    asyncio.run(scenario())


def test_manual_shutdown_does_not_schedule_rapid_startup_retry(runtime):
    runtime.process = SimpleNamespace(returncode=-11, stdout=SimpleNamespace(readline=AsyncMock(return_value=b"")),
                                      wait=AsyncMock(return_value=-11))
    runtime.closing = True
    asyncio.run(runtime.read_game(runtime.process))
    assert runtime.startup_failures == 0
    assert runtime.next_recovery == 0


@pytest.mark.parametrize("crossplay", [True, False])
def test_server_online_only_after_world_and_backend_are_ready(runtime, crossplay):
    async def scenario():
        runtime.process = SimpleNamespace(returncode=None)
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": crossplay})
        runtime.state = "iniciando"
        lines = iter([b"Game server connected\n", b"Loading: Done.\n",
                      b'Session "World" registered with join code 123456\n', b""])
        observed = []

        async def readline():
            observed.append(runtime.state)
            return next(lines)

        runtime.process.stdout = SimpleNamespace(readline=readline)
        runtime.process.wait = AsyncMock(return_value=0)
        await runtime.read_game(runtime.process)
        assert observed[:2] == ["iniciando", "iniciando"]
        assert observed[2] == ("iniciando" if crossplay else "online")
        assert observed[3] == "online"

    asyncio.run(scenario())


def test_initial_loading_does_not_trigger_maintenance(runtime):
    runtime.process = SimpleNamespace(returncode=None)
    assert not runtime.game_ready
    asyncio.run(runtime.tick())
    assert runtime.queue.empty()


def test_scheduled_failure_is_persisted_and_retried(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(returncode=None)
        runtime.game_ready = True
        runtime.execute = AsyncMock(side_effect=MaintenanceDeferred("occupied"))
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


def test_container_shutdown_stops_game_without_waiting_for_empty_players(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.clean_stop = False

        async def finish():
            runtime.last_save = time.monotonic()
            runtime.process.returncode = 0

        runtime.reader = asyncio.create_task(finish())
        with patch("app.runtime.os.killpg", create=True) as kill, patch("app.runtime.query_steam") as query:
            await runtime.shutdown()
        assert runtime.closing and not runtime.want_running
        assert runtime.clean_stop
        kill.assert_called_once_with(42, signal.SIGINT)
        query.assert_not_called()

    asyncio.run(scenario())


@pytest.mark.skipif(os.name != "posix", reason="Sinais e grupos de processos Linux necessários")
@pytest.mark.parametrize("safe", [True, False])
@pytest.mark.parametrize("saved", [False, "World saved", "World save (5/5) done. Total time [480ms]"])
def test_real_process_signals_require_save_confirmation(runtime, safe, saved):
    async def scenario():
        code = ('import signal,sys\n'
                'def stop(*args):\n'
                + (' print(' + repr(saved) + ', flush=True)\n' if saved else '')
                + ' sys.exit(0)\n'
                'signal.signal(signal.SIGINT, stop)\n'
                'print("ready", flush=True)\n'
                'while True: signal.pause()\n')
        child = await asyncio.create_subprocess_exec(sys.executable, "-u", "-c", code,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
        runtime.process = child
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
        runtime.clean_stop = False
        try:
            assert await asyncio.wait_for(child.stdout.readline(), timeout=5) == b"ready\n"
            runtime.reader = asyncio.create_task(runtime.read_game(child))
            runtime.players.consume("Connections 0 ZDOS:1")
            runtime.players.empty_since -= 121
            if saved:
                await asyncio.wait_for(runtime.stop(safe=safe), timeout=5)
                assert runtime.clean_stop
            else:
                with pytest.raises(RuntimeError, match="salvamento"):
                    await asyncio.wait_for(runtime.stop(safe=safe), timeout=5)
                assert not runtime.clean_stop
            assert child.returncode == 0
        finally:
            if child.returncode is None:
                os.killpg(child.pid, signal.SIGKILL)
                await child.wait()
            if runtime.reader:
                await asyncio.gather(runtime.reader, return_exceptions=True)

    asyncio.run(scenario())


def test_stop_finishes_when_game_exited_but_stdout_stays_open(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.clean_stop = False
        runtime.reader = asyncio.create_task(asyncio.Event().wait())

        def finish(*_):
            runtime.process.returncode = 0
            runtime.last_save = time.monotonic()

        with patch("app.runtime.os.killpg", side_effect=finish, create=True), patch("app.runtime.LOG_DRAIN_TIMEOUT", 0.01):
            await asyncio.wait_for(runtime.stop(safe=False), timeout=1)
        assert runtime.clean_stop
        assert runtime.reader.cancelled()
        assert runtime.state == "parado"

    asyncio.run(scenario())


def test_cancelled_stop_is_resumed_without_repeating_signal_or_deadline(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.clean_stop = False
        signalled, finish = asyncio.Event(), asyncio.Event()

        async def reader():
            await finish.wait()
            runtime.last_save = time.monotonic()
            runtime.process.returncode = 0

        runtime.reader = asyncio.create_task(reader())
        with patch("app.runtime.os.killpg", side_effect=lambda *_: signalled.set(), create=True) as kill:
            original = asyncio.create_task(runtime.stop(safe=False))
            await signalled.wait()
            requested = runtime.stop_request
            original.cancel()
            await asyncio.gather(original, return_exceptions=True)
            assert not runtime.reader.cancelled()
            resumed = asyncio.create_task(runtime.stop(safe=False))
            await asyncio.sleep(0)
            finish.set()
            await resumed
            assert runtime.stop_request == requested
            kill.assert_called_once_with(42, signal.SIGINT)
        assert runtime.clean_stop

    asyncio.run(scenario())


def test_stop_timeout_never_kills_or_marks_world_clean(runtime):
    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.clean_stop = False
        runtime.reader = asyncio.create_task(asyncio.Event().wait())
        with patch("app.runtime.os.killpg", create=True) as kill, patch("app.runtime.GAME_STOP_TIMEOUT", 0.02):
            with pytest.raises(RuntimeError, match="Parada excedeu"):
                await runtime.stop(safe=False)
            with pytest.raises(RuntimeError, match="Parada excedeu"):
                await runtime.stop(safe=False)
            kill.assert_called_once_with(42, signal.SIGINT)
        assert not runtime.clean_stop
        assert runtime.live
        runtime.reader.cancel()
        await asyncio.gather(runtime.reader, return_exceptions=True)

    asyncio.run(scenario())


def test_shutdown_cancels_metadata_query_without_waiting_two_minutes(runtime):
    async def scenario():
        started = asyncio.Event()

        async def execute(_):
            started.set()
            await asyncio.Event().wait()

        runtime.execute = execute
        runtime.schedule_once("check-update")
        runtime.worker_task = asyncio.create_task(runtime.worker())
        await started.wait()
        await asyncio.wait_for(runtime.shutdown(), timeout=1)
        assert runtime.worker_task.done()
        assert runtime.active is None

    asyncio.run(scenario())


@pytest.mark.parametrize("action", ["backup", "restart", "update"])
@pytest.mark.parametrize("complete", [True, False])
def test_chunked_save_shutdown_drives_maintenance_safely(runtime, action, complete):
    async def scenario():
        lines = asyncio.Queue()
        process = SimpleNamespace(pid=42, returncode=None, stdout=SimpleNamespace(readline=lines.get))

        async def wait():
            return process.returncode

        process.wait = wait
        runtime.process = process
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
        runtime.clean_stop = False
        runtime.clean_marker.write_text("{}")
        runtime.players.consume("Connections 0 ZDOS:81")
        runtime.players.empty_since -= 121
        # A successful earlier autosave must not authorize this shutdown.
        runtime.last_save = time.monotonic() - 60
        runtime.reader = asyncio.create_task(runtime.read_game(process))
        runtime.snapshot = AsyncMock(return_value="saved.tar.gz")
        runtime.install = AsyncMock()
        runtime.start = AsyncMock()

        def signal_game(_pid, sig):
            if sig == signal.SIGINT:
                for stage in ("World save (1/5) Cloud & Backup checks done [11ms]",
                              "World save (2/5) Chunks writing done [330ms]",
                              "World save (3/5) DB2 writing done [91ms]",
                              "World save (4/5) FWL writing done [6ms]"):
                    lines.put_nowait((stage + "\n").encode())
                if complete:
                    lines.put_nowait(b"World save (5/5) done. Total time [480ms]\n")
                process.returncode = 0
                lines.put_nowait(b"")

        with patch("app.runtime.os.killpg", side_effect=signal_game, create=True), \
                patch("app.runtime.signal.SIGSTOP", 19, create=True), patch("app.runtime.signal.SIGCONT", 18, create=True):
            if complete:
                await runtime.execute({"action": action})
                runtime.snapshot.assert_awaited_once_with(action)
                runtime.start.assert_awaited_once()
                assert runtime.install.await_count == (1 if action == "update" else 0)
                assert not runtime.clean_marker.exists()
            else:
                with pytest.raises(RuntimeError, match="salvamento"):
                    await runtime.execute({"action": action})
                runtime.snapshot.assert_not_awaited()
                runtime.install.assert_not_awaited()
                runtime.start.assert_not_awaited()
                assert runtime.clean_marker.exists()
        assert runtime.clean_stop is complete

    asyncio.run(scenario())


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
                with pytest.raises(RuntimeError, match="salvamento"):
                    await runtime.stop()
                assert not runtime.clean_stop and runtime.clean_marker.exists()
            assert [call.args[1] for call in kill.call_args_list] == [19, signal.SIGINT, 18]

    asyncio.run(scenario())


async def finish_worker(runtime):
    worker = asyncio.create_task(runtime.worker())
    await runtime.queue.join()
    worker.cancel()
    await asyncio.gather(worker, return_exceptions=True)


@pytest.mark.parametrize("action", ["restart", "backup", "update"])
def test_failed_maintenance_after_stop_is_never_replayed(runtime, action):
    async def scenario():
        runtime.process = SimpleNamespace(returncode=None)

        async def stop():
            runtime.process.returncode = 0

        async def start():
            runtime.process = SimpleNamespace(returncode=None)

        runtime.stop = AsyncMock(side_effect=stop)
        runtime.start = AsyncMock(side_effect=start)
        runtime.snapshot = AsyncMock(side_effect=OSError("disk full"))
        runtime.game_ready = True
        runtime.schedule_once(action)
        await finish_worker(runtime)
        assert runtime.jobs[0]["state"] == "falha"
        runtime.next_retry = 0
        for _ in range(10):
            await runtime.tick()
        assert runtime.queue.empty()
        runtime.stop.assert_awaited_once()
        runtime.start.assert_awaited_once()
        assert Runtime(runtime.data, runtime.config).queue.empty()

    asyncio.run(scenario())


def test_inflight_maintenance_is_not_replayed_after_supervisor_restart(runtime):
    runtime.schedule_once("restart")
    runtime.jobs[0]["state"] = "executando"
    runtime.persist_pending()
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.empty()
    assert restored.jobs[0]["state"] == "falha"
    assert "interrompida" in restored.jobs[0]["error"]


def test_manual_stop_cancels_pending_automatic_work(runtime):
    async def scenario():
        runtime.schedule_once("restart")
        runtime.schedule_once("check-update")
        await runtime.execute({"action": "stop"})
        runtime.execute = AsyncMock()
        await finish_worker(runtime)
        runtime.execute.assert_not_awaited()
        assert all(job["state"] == "cancelado" for job in runtime.jobs)
        await runtime.tick()
        assert runtime.queue.empty()
        assert Runtime(runtime.data, runtime.config).queue.empty()

    asyncio.run(scenario())


def test_repeated_manual_clicks_do_not_duplicate_jobs(runtime):
    first = runtime.submit("restart")
    assert runtime.submit("restart") is first
    assert runtime.queue.qsize() == 1


def test_completed_manual_backup_satisfies_pending_daily_restart(runtime):
    async def scenario():
        runtime.schedule_once("restart")
        runtime.stop = AsyncMock()
        runtime.snapshot = AsyncMock(return_value="backup.tar.gz")
        await runtime.execute({"action": "backup"})
        assert runtime.jobs[0]["state"] == "cancelado"
        runtime.execute = AsyncMock()
        await finish_worker(runtime)
        runtime.execute.assert_not_awaited()

    asyncio.run(scenario())


def test_repeated_short_sessions_suspend_recovery_until_manual_start(runtime):
    async def scenario():
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": False})
        for _ in range(5):
            runtime.game_ready = runtime.world_loaded = runtime.transport_ready = False
            runtime.ready_since = None
            runtime.process = steam_child(-11, [b"Opened Steam server", b"Loading: Done."])
            await runtime.read_game(runtime.process)
        assert runtime.startup_failures == 5
        assert not runtime.status()["automatic_recovery"]
        assert runtime.status()["recovery_error"]
        runtime.next_recovery = 0
        await runtime.tick()
        assert runtime.queue.empty()
        runtime.start = AsyncMock()
        await runtime.execute({"action": "start"})
        assert runtime.want_running and runtime.startup_failures == 0
        assert runtime.recovery_error is None

    asyncio.run(scenario())


def test_stable_session_resets_consecutive_crash_counter(runtime):
    runtime.startup_failures = 4
    runtime.game_ready = True
    runtime.ready_since = time.monotonic() - 601
    runtime.process = steam_child(-11)
    asyncio.run(runtime.read_game(runtime.process))
    assert runtime.want_running and runtime.startup_failures == 1


@pytest.mark.parametrize("started_before", [False, True])
def test_daily_maintenance_only_runs_when_schedule_is_crossed(runtime, started_before):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo

    async def scenario():
        now = datetime(2026, 11, 13, 12, 0, tzinfo=ZoneInfo("America/Sao_Paulo"))
        runtime.config.overrides.update({"restart_at": "05:00", "backup_at": "05:00"})
        runtime.schedule_started = now - timedelta(hours=8 if started_before else 1)
        runtime.process = SimpleNamespace(returncode=None)
        runtime.game_ready = True
        with patch("app.runtime.datetime") as clock:
            clock.now.return_value = now
            for _ in range(10):
                await runtime.tick()
        assert runtime.queue.qsize() == int(started_before)
        if started_before:
            assert runtime.jobs[0]["action"] == "restart"

    asyncio.run(scenario())


def test_failed_update_build_is_not_automatically_scheduled_again(runtime):
    async def scenario():
        runtime.latest_build = "2"
        runtime.installed_build = lambda: "1"
        runtime.execute = AsyncMock(side_effect=OSError("download failed"))
        runtime.schedule_once("update")
        await finish_worker(runtime)
        del runtime.execute
        runtime.available_build = AsyncMock(return_value="2")
        await runtime.execute({"action": "check-update"})
        assert runtime.queue.empty()
        runtime.available_build.return_value = "3"
        await runtime.execute({"action": "check-update"})
        assert runtime.queue.qsize() == 1

    asyncio.run(scenario())


def test_already_installed_scheduled_update_does_not_stop_server(runtime):
    runtime.latest_build = "2"
    runtime.installed_build = lambda: "2"
    runtime.stop = AsyncMock()
    asyncio.run(runtime.execute({"action": "update", "scheduled": True}))
    runtime.stop.assert_not_awaited()


def test_persisted_maintenance_waits_for_game_readiness(runtime):
    async def scenario():
        runtime.schedule_once("restart")
        restored = Runtime(runtime.data, runtime.config)
        restored.stop = AsyncMock()
        await finish_worker(restored)
        assert restored.jobs[0]["state"] == "adiado"
        restored.stop.assert_not_awaited()
        restored.process = SimpleNamespace(returncode=None)
        restored.next_retry = 0
        await restored.tick()
        assert restored.queue.empty()
        restored.game_ready = True
        await restored.tick()
        assert restored.queue.qsize() == 1

    asyncio.run(scenario())


def test_shutdown_does_not_resurrect_cancelled_maintenance(runtime):
    async def scenario():
        runtime.schedule_once("restart")
        await runtime.execute({"action": "stop"})
        runtime.worker_task = asyncio.create_task(runtime.worker())
        await runtime.shutdown()
        assert runtime.jobs[0]["state"] == "cancelado"
        assert Runtime(runtime.data, runtime.config).queue.empty()

    asyncio.run(scenario())
