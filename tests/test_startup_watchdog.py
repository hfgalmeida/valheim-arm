import asyncio
import signal
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.startup_watchdog import StartupWatchdog, world_ready

FATAL = "Caught fatal signal - signo:11 code:2 errno:0 addr:0x1234"


def failed_process(runtime, *, world=False, scene=True):
    process = SimpleNamespace(pid=4242, returncode=None)
    runtime.process = process
    runtime.clean_stop = False
    runtime.clean_marker.write_text('{"started":1}')
    runtime.startup_watchdog.reset(process)
    if scene:
        runtime.startup_watchdog.consume("Loading first scene!")
    if world:
        runtime.startup_watchdog.consume("Loading: ZNet Start")
    runtime.startup_watchdog.consume(FATAL)
    return process


def test_only_explicit_fatal_first_scene_allows_forced_cleanup():
    guard = StartupWatchdog()
    assert not guard.safe_to_terminate
    assert not guard.consume("A player said: " + FATAL)
    assert guard.consume(FATAL)
    assert not guard.safe_to_terminate
    assert not guard.consume(FATAL)
    guard.reset(None)
    guard.consume("Loading first scene!")
    guard.consume(FATAL)
    assert guard.safe_to_terminate
    guard.consume("Loading: ZNet Start")
    assert not guard.safe_to_terminate


@pytest.mark.parametrize(
    "line",
    [
        "Loading: ZNet Start",
        "ZNet.LoadWorld: world",
        "ZDOMan.LoadChunks",
        "World saved",
        "World save (1/5) started",
        "Loading: Done.",
    ],
)
def test_world_activity_blocks_forced_termination(line):
    guard = StartupWatchdog()
    guard.consume("Loading first scene!")
    guard.consume(line)
    guard.consume(FATAL)
    assert not guard.safe_to_terminate


def test_initial_unity_loading_done_is_not_world_ready():
    assert not world_ready("Loading: Done, Total time: -11.930698")
    assert world_ready("Loading: Done. Total seconds sinze ZNet start: 80")


def test_fatal_detection_is_ignored_during_requested_shutdown():
    guard = StartupWatchdog()
    guard.consume("Loading first scene!")
    assert not guard.consume(FATAL, allow_fatal=False)
    assert not guard.fatal


def test_tick_queues_one_serialized_recovery_and_no_restart(runtime):
    failed_process(runtime)

    async def scenario():
        await runtime.tick()
        await runtime.tick()

    asyncio.run(scenario())
    assert runtime.queue.qsize() == 1
    assert runtime.jobs[-1]["action"] == "recover-startup"
    assert runtime.diagnostics()["phase"] == "failed"


def test_manual_stop_cancels_queued_fatal_recovery(runtime):
    failed_process(runtime)
    asyncio.run(runtime.tick())
    runtime.submit("stop")
    assert not runtime.want_running
    assert runtime.jobs[0]["state"] == "cancelado"
    with patch("app.runtime.os.killpg", create=True) as send:
        asyncio.run(runtime.recover_startup())
        send.assert_not_called()


@pytest.mark.parametrize("world,scene", [(True, True), (False, False)])
def test_unsafe_or_unknown_phase_suspends_without_signalling(runtime, world, scene):
    failed_process(runtime, world=world, scene=scene)
    with patch("app.runtime.os.killpg", create=True) as send:
        asyncio.run(runtime.recover_startup())
    send.assert_not_called()
    assert not runtime.want_running
    assert runtime.recovery_error
    assert not runtime.clean_stop
    assert runtime.clean_marker.exists()


def test_graceful_fatal_exit_counts_one_failure_and_preserves_saves(runtime):
    process = failed_process(runtime)
    save = runtime.data / "saves/world.db"
    save.write_bytes(b"last-clean-world")

    def send(pid, sig):
        assert pid == process.pid and sig == signal.SIGINT
        process.returncode = 0

    with patch("app.runtime.os.killpg", side_effect=send, create=True) as kill:
        asyncio.run(runtime.recover_startup())
    kill.assert_called_once()
    assert runtime.startup_failures == 1
    assert 13 <= runtime.next_recovery - time.monotonic() <= 15
    assert runtime.clean_marker.exists() and not runtime.clean_stop
    assert save.read_bytes() == b"last-clean-world"
    assert not list(runtime.backups.root.glob("*.tar.gz"))


def test_stuck_pre_world_fatal_is_killed_then_retried(runtime):
    process = failed_process(runtime)

    def send(pid, sig):
        if sig == 9:
            process.returncode = -9

    with (
        patch("app.runtime.os.killpg", side_effect=send, create=True) as kill,
        patch("app.runtime.signal.SIGKILL", 9, create=True),
        patch("app.runtime.STARTUP_FATAL_GRACE", 0),
    ):
        asyncio.run(runtime.recover_startup())
    assert [call.args for call in kill.call_args_list] == [(4242, signal.SIGINT), (4242, 9)]
    assert runtime.startup_failures == 1
    assert runtime.clean_marker.exists() and not runtime.clean_stop


def test_world_progress_during_grace_cancels_escalation(runtime):
    failed_process(runtime)

    async def progress(*args):
        runtime.startup_watchdog.consume("ZNet.LoadWorld: world")
        return False

    runtime.wait_game_exit = AsyncMock(side_effect=progress)
    with patch("app.runtime.os.killpg", create=True) as send:
        asyncio.run(runtime.recover_startup())
    send.assert_called_once_with(4242, signal.SIGINT)
    assert runtime.recovery_error and not runtime.want_running


def test_recovery_limit_is_shared_with_ordinary_crashes(runtime):
    async def scenario():
        for _ in range(5):
            process = failed_process(runtime)
            with patch(
                "app.runtime.os.killpg",
                side_effect=lambda *_: setattr(process, "returncode", 0),
                create=True,
            ):
                await runtime.recover_startup()

    asyncio.run(scenario())
    assert runtime.startup_failures == 5
    assert not runtime.want_running and runtime.recovery_error
    assert runtime.queue.empty()


def test_no_restart_if_manual_stop_arrives_during_cleanup(runtime):
    process = failed_process(runtime)

    def send(*args):
        runtime.submit("stop")
        process.returncode = 0

    with patch("app.runtime.os.killpg", side_effect=send, create=True):
        asyncio.run(runtime.recover_startup())
    assert not runtime.want_running
    assert runtime.startup_failures == 0
    assert [job["action"] for job in runtime.jobs] == ["stop"]


def test_process_that_does_not_exit_after_kill_suspends(runtime):
    failed_process(runtime)
    with (
        patch("app.runtime.os.killpg", create=True),
        patch("app.runtime.signal.SIGKILL", 9, create=True),
        patch("app.runtime.STARTUP_FATAL_GRACE", 0),
        patch("app.runtime.STARTUP_KILL_TIMEOUT", 0),
    ):
        asyncio.run(runtime.recover_startup())
    assert runtime.recovery_error
    assert not runtime.want_running
    assert runtime.live


def test_fatal_read_log_never_becomes_online_or_retries_twice(runtime):
    async def scenario():
        process = SimpleNamespace(pid=4242, returncode=None)
        runtime.process = process
        runtime.running = runtime.config.effective()
        lines = asyncio.Queue()
        for line in ("Loading first scene!", FATAL, "Opened Steam server", "Loading: Done."):
            lines.put_nowait((line + "\n").encode())
        process.stdout = SimpleNamespace(readline=lines.get)

        async def wait():
            return process.returncode

        process.wait = wait
        runtime.reader = asyncio.create_task(runtime.read_game(process))
        for _ in range(8):
            await asyncio.sleep(0)
        assert not runtime.game_ready
        assert runtime.diagnostics()["phase"] == "failed"
        # These late world logs also prohibit SIGKILL.
        assert not runtime.startup_watchdog.safe_to_terminate
        process.returncode = -11
        lines.put_nowait(b"")
        await runtime.reader
        assert runtime.startup_failures == 1

    asyncio.run(scenario())


def test_reader_pipe_left_open_is_bounded_during_cleanup(runtime):
    async def scenario():
        process = failed_process(runtime)
        runtime.reader = asyncio.create_task(asyncio.Event().wait())
        with (
            patch(
                "app.runtime.os.killpg",
                side_effect=lambda *_: setattr(process, "returncode", 0),
                create=True,
            ),
            patch("app.runtime.LOG_DRAIN_TIMEOUT", 0.01),
        ):
            await runtime.recover_startup()
        assert runtime.reader.cancelled()
        assert runtime.startup_failures == 1
        assert not runtime.clean_stop

    asyncio.run(scenario())


def test_shutdown_during_grace_never_escalates_or_retries(runtime):
    failed_process(runtime)

    async def closing(*args):
        runtime.closing = True
        runtime.want_running = False
        return False

    runtime.wait_game_exit = AsyncMock(side_effect=closing)
    with patch("app.runtime.os.killpg", create=True) as send:
        asyncio.run(runtime.recover_startup())
    send.assert_called_once_with(4242, signal.SIGINT)
    assert runtime.startup_failures == 0


def test_recovery_is_not_persisted_or_blocked_by_disk_errors(runtime):
    async def scenario():
        process = failed_process(runtime)
        with (
            patch("app.runtime.atomic_json", side_effect=OSError("disk full")),
            patch(
                "app.runtime.os.killpg",
                side_effect=lambda *_: setattr(process, "returncode", 0),
                create=True,
            ) as send,
        ):
            await runtime.tick()
            assert runtime.persistence_error
            worker = asyncio.create_task(runtime.worker())
            await runtime.queue.join()
            worker.cancel()
            await asyncio.gather(worker, return_exceptions=True)
            send.assert_called_once_with(4242, signal.SIGINT)
        assert runtime.startup_failures == 1

    asyncio.run(scenario())
    runtime.persistence_error = None
    runtime.persist_pending()
    import json

    assert json.loads(runtime.pending_path.read_text()) == []


def test_old_process_cannot_reset_current_watchdog_or_game_state(runtime):
    current = SimpleNamespace(pid=5555, returncode=None)
    runtime.process = current
    runtime.startup_watchdog.reset(current)
    runtime.game_ready = True
    runtime.state = "online"
    old = SimpleNamespace(
        pid=4444,
        returncode=-11,
        stdout=SimpleNamespace(readline=AsyncMock(return_value=(FATAL + "\n").encode())),
        wait=AsyncMock(return_value=-11),
    )
    asyncio.run(runtime.read_game(old))
    old.stdout.readline.assert_not_awaited()
    assert runtime.startup_watchdog.process is current
    assert not runtime.startup_watchdog.fatal
    assert runtime.game_ready and runtime.state == "online"
    assert runtime.startup_failures == 0


def test_process_ownership_is_rechecked_before_escalating(runtime):
    failed_process(runtime)
    replacement = SimpleNamespace(pid=5555, returncode=None)

    async def changed(*args):
        runtime.process = replacement
        runtime.startup_watchdog.reset(replacement)
        return False

    runtime.wait_game_exit = AsyncMock(side_effect=changed)
    with patch("app.runtime.os.killpg", create=True) as send:
        asyncio.run(runtime.recover_startup())
    send.assert_called_once_with(4242, signal.SIGINT)
    assert runtime.process is replacement
    assert runtime.startup_failures == 0
    assert not runtime.recovery_error
