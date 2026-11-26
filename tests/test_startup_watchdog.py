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
    assert runtime.jobs[0]["state"] == "cancelled"
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


@pytest.mark.parametrize("world,scene", [(True, True), (False, False)])
@pytest.mark.parametrize("code", [-9, -11, 0])
def test_suspended_fatal_recovers_only_after_confirmed_exit(runtime, world, scene, code):
    async def scenario():
        process = failed_process(runtime, world=world, scene=scene)
        process.stdout = asyncio.StreamReader()
        process.wait = AsyncMock(side_effect=lambda: process.returncode)
        runtime.reader = asyncio.create_task(runtime.read_game(process))
        save = runtime.data / "saves/world.db"
        save.write_bytes(b"last-clean-world")
        runtime.backups.create = lambda *_: pytest.fail("No backup after a fatal exit")
        runtime.backups.restore = lambda *_: pytest.fail("Never restore automatically")
        with patch("app.runtime.os.killpg", create=True) as send:
            await runtime.recover_startup()
            await runtime.tick()
            assert not runtime.want_running and runtime.queue.empty()
            assert runtime.recovery_after_exit[0] is process
            send.assert_not_called()
            process.returncode = code
            process.stdout.feed_eof()
            await runtime.reader
            send.assert_not_called()
        assert runtime.want_running and not runtime.recovery_error
        assert runtime.recovery_after_exit is None
        assert runtime.startup_failures == 1
        assert runtime.clean_marker.exists() and not runtime.clean_stop
        assert save.read_bytes() == b"last-clean-world"
        await runtime.tick()
        assert runtime.queue.empty()  # The backoff still applies.
        runtime.next_recovery = 0
        await runtime.tick()
        await runtime.tick()
        assert runtime.queue.qsize() == 1
        assert runtime.jobs[-1]["action"] == "start"

    asyncio.run(scenario())


@pytest.mark.parametrize("interruption", ["manual-stop", "shutdown", "changed-intent", "limit"])
def test_suspended_fatal_exit_respects_stop_preference_and_failure_limit(runtime, interruption):
    async def scenario():
        process = failed_process(runtime, world=True)
        process.stdout = asyncio.StreamReader()
        process.wait = AsyncMock(side_effect=lambda: process.returncode)
        runtime.reader = asyncio.create_task(runtime.read_game(process))
        await runtime.recover_startup()
        if interruption == "manual-stop":
            runtime.submit("stop")
        elif interruption == "shutdown":
            runtime.closing = True
        elif interruption == "changed-intent":
            runtime.run_intent.set(False)
            runtime.run_intent.set(True)
        else:
            runtime.startup_failures = 4
        process.returncode = -9
        process.stdout.feed_eof()
        await runtime.reader
        runtime.next_recovery = 0
        await runtime.tick()
        assert not runtime.want_running
        assert not any(job["action"] == "start" for job in runtime.jobs)
        if interruption == "limit":
            assert runtime.startup_failures == 5
            assert "five consecutive" in runtime.recovery_error

    asyncio.run(scenario())


def test_suspended_recovery_cannot_resume_for_live_or_replaced_process(runtime):
    process = failed_process(runtime, world=True)
    asyncio.run(runtime.recover_startup())
    assert not runtime.resume_recovery_after_exit(process)
    assert not runtime.want_running
    assert runtime.recovery_after_exit[0] is process
    process.returncode = -9
    runtime.process = SimpleNamespace(pid=5555, returncode=None)
    assert not runtime.resume_recovery_after_exit(process)
    assert not runtime.want_running


def test_identical_fatal_spam_is_summarized_without_hiding_other_lines(runtime):
    async def scenario():
        process = SimpleNamespace(pid=4242, returncode=-9)
        runtime.process = process
        process.stdout = asyncio.StreamReader()
        process.wait = AsyncMock(return_value=-9)
        process.stdout.feed_data(
            ("Loading first scene!\n" + (FATAL + "\n") * 1000 + "A different diagnostic\n").encode()
        )
        process.stdout.feed_eof()
        await runtime.read_game(process)
        messages = [entry["message"] for entry in runtime.logs]
        assert messages.count(FATAL) == 1
        assert "Suppressed 999 identical fatal-signal lines" in messages
        assert "A different diagnostic" in messages
        assert "Game process exited: -9" in messages
        assert len(messages) < 15
        assert runtime.startup_failures == 1

    asyncio.run(scenario())


def test_late_world_progress_exit_is_counted_once_after_suspended_cleanup(runtime):
    async def scenario():
        process = failed_process(runtime)
        process.stdout = asyncio.StreamReader()
        process.wait = AsyncMock(side_effect=lambda: process.returncode)
        runtime.reader = asyncio.create_task(runtime.read_game(process))

        async def progress(*args):
            runtime.startup_watchdog.consume("ZNet.LoadWorld: world")
            return False

        runtime.wait_game_exit = AsyncMock(side_effect=progress)
        with patch("app.runtime.os.killpg", create=True) as send:
            await runtime.recover_startup()
            send.assert_called_once_with(process.pid, signal.SIGINT)
        assert runtime.stop_request[0] is process
        process.returncode = -9
        process.stdout.feed_eof()
        await runtime.reader
        assert runtime.stop_request is None
        assert runtime.want_running and runtime.startup_failures == 1
        assert not runtime.recovery_error
        assert runtime.clean_marker.exists() and not runtime.clean_stop

    asyncio.run(scenario())
