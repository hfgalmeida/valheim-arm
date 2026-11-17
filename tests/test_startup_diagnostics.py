import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.game_logs import read_game_line
from app.startup_diagnostics import StartupDiagnostics, COMPATIBILITY_ENV, LIMIT
from app.startup_watchdog import StartupWatchdog
from tests.test_startup_watchdog import FATAL


def test_compatibility_after_early_fatal_is_isolated_from_parent(tmp_path, monkeypatch):
    monkeypatch.setenv("FEX_GAME_MODE", "auto")
    monkeypatch.setenv("FEX_MULTIBLOCK", "1")
    original_smc = os.environ.get("FEX_SMCCHECKS")
    diagnostics = StartupDiagnostics(tmp_path)
    guard = StartupWatchdog()
    guard.consume("Loading first scene!")
    diagnostics.begin("standard", SimpleNamespace(crossplay=False, bepinex=True))
    guard.consume(FATAL)
    diagnostics.observe(FATAL, guard)
    diagnostics.observe("#0  0x123 in PlatformInitializer:EarlyInitialize ()", guard)
    diagnostics.finish(-11, guard)
    profile, env = diagnostics.environment()
    assert profile == "compatible"
    assert all(env[key] == value for key, value in COMPATIBILITY_ENV.items())
    assert diagnostics.history[-1]["signature"] == "platform_initializer"
    assert os.environ["FEX_MULTIBLOCK"] == "1"
    assert os.environ.get("FEX_SMCCHECKS") == original_smc
    assert StartupDiagnostics(tmp_path).history == diagnostics.history
    assert not StartupDiagnostics(tmp_path).compatibility_requested


@pytest.mark.parametrize("phase", ["world", "unknown", "before_scene"])
def test_world_or_unknown_crashes_do_not_select_early_fatal_profile(tmp_path, phase):
    diagnostics = StartupDiagnostics(tmp_path)
    guard = StartupWatchdog()
    if phase != "before_scene":
        guard.consume("Loading first scene!")
    if phase == "world":
        guard.consume("ZNet.LoadWorld: Test")
    if phase == "unknown":
        guard.phase_unknown = True
    guard.consume(FATAL)
    diagnostics.finish(-11, guard)
    assert not diagnostics.compatibility_requested


def test_explicit_profile_override_and_invalid_mode(tmp_path, monkeypatch):
    diagnostics = StartupDiagnostics(tmp_path)
    diagnostics.compatibility_requested = True
    monkeypatch.setenv("FEX_GAME_MODE", "standard")
    assert diagnostics.environment()[0] == "standard"
    monkeypatch.setenv("FEX_GAME_MODE", "compatible")
    assert diagnostics.environment()[0] == "compatible"
    monkeypatch.setenv("FEX_GAME_MODE", "invalid")
    with pytest.raises(ValueError, match="FEX_GAME_MODE"):
        diagnostics.environment()


def test_history_bounded_and_disk_errors_do_not_prevent_cleanup(tmp_path):
    diagnostics = StartupDiagnostics(tmp_path)
    guard = StartupWatchdog()
    settings = SimpleNamespace(crossplay=False, bepinex=False)
    for _ in range(LIMIT + 2):
        diagnostics.begin("standard", settings)
        diagnostics.ready()
        diagnostics.finish(0, guard, expected=True)
    assert len(StartupDiagnostics(tmp_path).history) == LIMIT
    diagnostics.begin("compatible", settings)
    assert StartupDiagnostics(tmp_path).history[-1]["outcome"] == "interrupted"
    with patch("app.startup_diagnostics.atomic_json", side_effect=OSError("disk full")):
        diagnostics.finish(-11, guard)
    assert diagnostics.error and diagnostics.history[-1]["exit_code"] == -11
    assert "server_password" not in json.dumps(diagnostics.describe())


def test_malformed_history_is_ignored_and_spawn_failure_is_recorded(runtime):
    path = runtime.data / "config/startup-history.json"
    path.write_text('[{"started":NaN,"outcome":"ready"}]')
    assert StartupDiagnostics(runtime.data).history == []
    path.write_text("not-json")
    assert StartupDiagnostics(runtime.data).error
    install = runtime.data / "installs/test"
    install.mkdir()
    runtime.current_install = lambda: install

    async def scenario():
        with (
            patch("app.runtime.install_bepinex"),
            patch(
                "app.runtime.asyncio.create_subprocess_exec",
                AsyncMock(side_effect=OSError("cannot spawn")),
            ),
        ):
            with pytest.raises(OSError):
                await runtime.start()

    asyncio.run(scenario())
    record = runtime.startup_diagnostics.describe()["history"][0]
    assert record["outcome"] == "spawn_failed"
    assert record["finished"] is not None
    assert runtime.config.effective().server_password not in json.dumps(record)


def test_runtime_applies_fallback_to_next_game_process_only(runtime, monkeypatch):
    monkeypatch.setenv("FEX_GAME_MODE", "auto")
    install = runtime.data / "installs/test"
    install.mkdir()
    runtime.current_install = lambda: install
    children = []
    for code, lines in [
        (-11, [b"Loading first scene!\n", (FATAL + "\n").encode(), b""]),
        (0, [b""]),
    ]:
        children.append(
            SimpleNamespace(
                returncode=code,
                stdout=SimpleNamespace(readline=AsyncMock(side_effect=lines)),
                wait=AsyncMock(return_value=code),
            )
        )

    async def scenario():
        with (
            patch("app.runtime.install_bepinex"),
            patch(
                "app.runtime.asyncio.create_subprocess_exec", AsyncMock(side_effect=children)
            ) as spawn,
        ):
            await runtime.start()
            await runtime.reader
            assert runtime.startup_diagnostics.compatibility_requested
            assert "signo:11" in runtime.last_failure["message"]
            await runtime.start()
            await runtime.reader
            assert all(
                spawn.await_args_list[1].kwargs["env"][key] == value
                for key, value in COMPATIBILITY_ENV.items()
            )
            assert runtime.startup_failures == 2
            assert [record["profile"] for record in runtime.startup_diagnostics.history] == [
                "standard",
                "compatible",
            ]

    asyncio.run(scenario())
    assert not list(runtime.backups.root.glob("*.tar.gz"))
    assert runtime.clean_marker.exists()


def test_oversized_line_tail_cannot_forge_snapshot_and_reader_continues(runtime):
    async def scenario():
        stream = asyncio.StreamReader(limit=128)
        stream.feed_data(
            b"Loading first scene!\n"
            + b"x" * 1000
            + b"Connections 0 ZDOS:1\n"
            + b"Opened Steam server\n"
            + b"Connections 0 ZDOS:0 sent:0 recv:0\n"
            + b"World save (5/5) done.\n"
        )
        stream.feed_eof()
        runtime.startup_diagnostics.begin("standard", runtime.config.effective())
        runtime.process = SimpleNamespace(
            returncode=0, stdout=stream, wait=AsyncMock(return_value=0)
        )
        await runtime.read_game(runtime.process)
        assert runtime.last_save > 0
        assert runtime.startup_watchdog.phase_unknown
        assert runtime.startup_diagnostics.history[-1]["phase"] == "unknown"
        assert not runtime.world_loaded
        assert any("Oversized game line" in entry["message"] for entry in runtime.logs)
        assert not any(entry["message"].startswith("x") for entry in runtime.logs)

    asyncio.run(scenario())


def test_oversized_line_at_eof_is_discarded_without_hanging():
    async def scenario():
        stream = asyncio.StreamReader(limit=10)
        stream.feed_data(b"x" * 100)
        stream.feed_eof()
        called = []
        assert await read_game_line(stream, lambda: called.append(True)) == b""
        assert called == [True]

    asyncio.run(scenario())
