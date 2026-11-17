import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.mod_diagnostics import ModDiagnostics
from tests.test_web import client_fixture


def test_log_evidence_keeps_attempts_separate_from_completion(tmp_path):
    mods = ModDiagnostics(tmp_path)
    mods.begin(True)
    mods.consume("[Message: BepInEx] BepInEx 5.4.23.2 - valheim_server")
    mods.consume("[Info   : BepInEx] Loading [Useful Plugin 1.2.3]")
    data = mods.describe(True, True)
    assert data["version"] == "5.4.23.2"
    assert data["state"] == "loading"
    assert data["plugins"] == [
        {"name": "Useful Plugin", "version": "1.2.3", "state": "loading_reported"}
    ]
    mods.consume("[Message: BepInEx] Chainloader startup complete")
    assert mods.describe(True, True)["state"] == "complete"
    # A completion line never upgrades an attempt to a proven working plugin.
    assert mods.describe(True, True)["plugins"][0]["state"] == "loading_reported"


def test_missing_dependencies_and_failed_load_are_retained(tmp_path):
    mods = ModDiagnostics(tmp_path)
    mods.begin(True)
    mods.consume("[Info : BepInEx] Loading [Broken 1.0.0]")
    mods.consume("[Error : BepInEx] Error loading [Broken 1.0.0] : missing assembly")
    mods.consume(
        "[Error : BepInEx] Could not load [Other 2.0] because it has missing dependencies: vendor.dep"
    )
    mods.consume(
        "[Warning : BepInEx] Skipping [Third 1.0] because it has a dependency that was not loaded."
    )
    mods.consume("[Message : BepInEx] Chainloader startup complete")
    assert all(plugin["state"] == "failed" for plugin in mods.plugins.values())
    assert len(mods.issues) == 3
    assert "vendor.dep" in mods.describe(True, True)["issues"][1]["message"]


def test_session_reset_and_disabled_ignore_historical_logs(tmp_path):
    mods = ModDiagnostics(tmp_path)
    assert mods.describe(False, True)["state"] == "awaiting_start"
    mods.begin(True)
    mods.consume("[Info : BepInEx] Loading [Old 1.0]")
    mods.begin(False)
    mods.consume("[Info : BepInEx] Loading [Old 1.0]")
    data = mods.describe(True, True)
    assert data["state"] == "disabled"
    assert data["pending_config"]
    assert not data["plugins"]
    mods.begin(True)
    assert mods.describe(True, True)["state"] == "unconfirmed"
    assert mods.describe(False, True)["state"] == "stopped"


def test_inventory_does_not_claim_a_dll_loaded(tmp_path):
    path = tmp_path / "mods/BepInEx/plugins/nested/file.dll"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"original")
    mods = ModDiagnostics(tmp_path)
    assert mods.file_state(path, False) == "awaiting_start"
    mods.begin(True)
    assert mods.file_state(path, True) == "present_at_start"
    assert mods.file_state(path, False) == "last_session"
    path.write_bytes(b"new-version")
    assert mods.file_state(path, False) == "pending_restart"
    mods.begin(True)
    assert mods.file_state(path, True) == "present_at_start"
    assert not mods.plugins


def test_bounded_deduplicated_errors_and_unrelated_game_logs(tmp_path):
    mods = ModDiagnostics(tmp_path)
    mods.begin(True)
    mods.consume("[Error : Unity Log] game error before BepInEx")
    assert not mods.issues
    mods.consume("[Message : BepInEx] BepInEx 5.4.23 - valheim")
    for index in range(75):
        line = f"[Error : APlugin] Error {index}"
        mods.consume(line)
        mods.consume(line)
    assert len(mods.issues) == 50
    for index in range(250):
        mods.consume(f"[Info : BepInEx] Loading [Plugin{index} 1.0]")
    assert len(mods.plugins) == 200
    mods.consume("Some ordinary world log mentions Loading [Fake 1.0]")
    assert len(mods.plugins) == 200


def test_runtime_consumes_redacted_logs(runtime):
    runtime.mod_diagnostics.begin(True)
    process = SimpleNamespace(
        returncode=0,
        stdout=SimpleNamespace(
            readline=AsyncMock(
                side_effect=[
                    b"[Message : BepInEx] BepInEx 5.4.23 - valheim\n",
                    b"[Error : BepInEx] Error loading [Secret 1.0] : game-password\n",
                    b"",
                ]
            )
        ),
        wait=AsyncMock(return_value=0),
    )
    runtime.process = process
    runtime.want_running = False
    asyncio.run(runtime.read_game(process))
    data = runtime.mod_diagnostics.describe(False, True)
    assert "game-password" not in str(data)
    assert "[password]" in data["issues"][0]["message"]


def test_plugin_endpoint_requires_auth_and_shows_pending_file(runtime, monkeypatch):
    from tests.test_plugins import upload
    from tests.plugin_fixture import dll
    from app.plugins import Plugins

    plugins = Plugins(runtime.data)
    upload(plugins, "sample.dll", dll())
    runtime.mod_diagnostics.begin(True)
    path = plugins.root / "sample.dll"
    path.write_bytes(dll(2))
    client = client_fixture(runtime, monkeypatch)
    response = client.get("/api/v1/plugins")
    assert response.status_code == 200
    assert response.json()["files"][0]["session_state"] == "pending_restart"
    assert response.json()["diagnostics"]["started"]
    assert "mods" in client.get("/api/v1/state").json()
    client.cookies.clear()
    assert client.get("/api/v1/plugins").status_code == 401


def test_inventory_permission_error_does_not_block_diagnosis(tmp_path):
    from unittest.mock import patch

    mods = ModDiagnostics(tmp_path)

    def fail_walk(*args, **kwargs):
        kwargs["onerror"](PermissionError("private folder"))

    with patch("app.mod_diagnostics.os.walk", side_effect=fail_walk):
        mods.begin(True)
    assert mods.scan_error
    mods.consume("[Message : BepInEx] BepInEx 5.4.23 - valheim")
    assert mods.describe(True, True)["version"] == "5.4.23"
    assert mods.file_state(tmp_path / "mods/BepInEx/plugins/a.dll", True) == "unknown"


def test_valheim_pack_real_console_format_with_guid_and_counts(tmp_path):
    mods = ModDiagnostics(tmp_path)
    mods.begin(True)
    mods.consume("\ufeff[Message:   BepInEx] BepInEx 5.4.23.6 - valheim_server")
    mods.consume(
        "[Info   :   BepInEx] Loading [Newtonsoft.Json Detector 1.0.0] (com.ValheimModding.NewtonsoftJsonDetector)"
    )
    mods.consume("[Info   :   BepInEx] Loading [Server Info 2.2.7] (Odin_Sons.ServerInfo)")
    mods.consume(
        "[Message:   BepInEx] Chainloader startup complete (2 loaded, 0 skipped, 0 failed)"
    )
    result = mods.describe(True, True)
    assert result["version"] == "5.4.23.6"
    assert result["state"] == "complete"
    assert len(result["plugins"]) == 2
    assert result["plugins"][1]["name"] == "Server Info"
    assert not result["issues"]
