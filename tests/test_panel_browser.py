"""Optional real-browser UI check with deterministic API fixtures, no game."""

import math
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Set RUN_BROWSER_TESTS=1 for browser checks",
)


def test_panel_login_forms_actions_and_mobile(runtime):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).resolve().parents[1] / "app/static"
    image_update = {"available": True, "state": "idle", "message": "Ready to update."}
    authenticated = False
    requests = []
    plugin_config = runtime.data / "mods/BepInEx/config/test.cfg"
    plugin_config.parent.mkdir(parents=True)
    plugin_config.write_text("Enabled = true")
    from app.mod_configs import ModConfigs

    mod_editor = ModConfigs(runtime.data)
    from app.plugins import Plugins
    from tests.plugin_fixture import dll

    plugin_files = Plugins(runtime.data)
    lists = {"admins": ["Steam_123"], "banned": [], "permitted": []}
    metric = {
        "time": time.time(),
        "cpu_percent": 72,
        "memory_bytes": 3 * 1024**3,
        "memory_limit": 10 * 1024**3,
        "network_in_bps": 2048,
        "network_out_bps": 4096,
        "disk_used": 8 * 1024**3,
        "disk_total": 50 * 1024**3,
        "scope": "container (cgroup v2)",
    }
    state = runtime.status()
    state.update(
        run_intent={"running": True, "error": None},
        state="online",
        running=True,
        crossplay=True,
        join_code="012345",
        startup={
            "history": [
                {
                    "started": 1794643216,
                    "outcome": "fatal",
                    "profile": "compatible",
                    "phase": "scene",
                    "signal": 11,
                    "exit_code": -11,
                    "signature": "platform_initializer",
                }
            ],
            "error": None,
        },
        players={
            "count": 2,
            "reason": "game snapshot",
            "source": "game_log",
            "updated_at": 1794643216,
            "age_seconds": 12,
        },
        mods={
            "state": "complete",
            "version": "5.4.23",
            "started": None,
            "pending_config": False,
            "scan_error": None,
            "plugins": [{"name": "Useful Plugin", "version": "1.2.3", "state": "loading_reported"}],
            "issues": [
                {
                    "level": "error",
                    "source": "BepInEx",
                    "message": "Could not load <unsafe> because it has missing dependencies",
                }
            ],
        },
        image_version={
            "installed": "0.14.0",
            "available": "0.15.0",
            "update_available": True,
            "error": None,
        },
        installed_build="123456",
        available_build="123456",
        jobs=[
            {
                "action": "restart",
                "state": "completed",
                "created": 1794643216,
                "started": 1794643217,
                "finished": 1794643227,
                "duration": 10,
                "attempts": 1,
                "origin": "administrator",
                "result": "backup.tar.gz",
            }
        ],
        metrics=[
            metric
            | {"time": metric["time"] - (29 - i) * 5, "cpu_percent": 50 + 30 * math.sin(i / 4)}
            for i in range(30)
        ],
    )

    def route(request):
        nonlocal authenticated
        path = request.request.url.removeprefix("https://panel.test")
        method = request.request.method
        if path.startswith("/api/v1"):
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(request.request.url).query)
            endpoint = path.removeprefix("/api/v1").split("?", 1)[0]
            if endpoint == "/session":
                request.fulfill(status=401, json={"detail": "Sign in"})
                return
            if endpoint == "/login":
                authenticated = True
                request.fulfill(json={"csrf": "browser-csrf"})
                return
            assert authenticated
            if method in ("POST", "PATCH", "PUT"):
                payload = None if endpoint == "/plugins" else request.request.post_data_json
                requests.append((endpoint, payload))
            if endpoint == "/config":
                if method == "PATCH":
                    runtime.config.change(
                        payload.get("changes", {}),
                        payload.get("reset", []),
                        payload.get("confirm_world", False),
                    )
                value = runtime.config.describe()
            elif endpoint == "/accounts":
                value = {
                    "users": [
                        {
                            "id": "admin",
                            "username": "admin",
                            "role": "admin",
                            "enabled": True,
                            "must_change_password": False,
                        }
                    ]
                }
            elif endpoint == "/audit":
                value = {"events": [], "error": None}
            elif endpoint == "/image-version":
                value = state["image_version"] | {"checked": time.time()}
            elif endpoint == "/image-update":
                if method == "POST":
                    assert payload["confirm"] is True and len(payload["request_id"]) == 32
                    image_update.update(state="running", message="Downloading a image Docker.")
                value = image_update.copy()
            elif endpoint == "/state":
                value = state
            elif endpoint == "/alerts":
                if method == "PUT":
                    runtime.alerts.configure(
                        payload["enabled"],
                        payload.get("url", ""),
                        payload.get("backup_max_age_hours"),
                        payload.get("cloud_max_age_hours"),
                    )
                value = runtime.alerts.describe()
            elif endpoint == "/alerts/test":
                value = {"sent": True}
            elif endpoint == "/cloud":
                if method == "PUT":
                    runtime.cloud.configure(
                        payload["enabled"], payload["folder"], payload.get("config", "")
                    )
                value = runtime.cloud.describe()
            elif endpoint == "/plugins":
                if method == "POST":
                    with plugin_files.staging(query["name"][0]) as (file, temporary):
                        file.write(request.request.post_data_buffer)
                        file.close()
                        value = plugin_files.commit(
                            query["name"][0], temporary, query.get("revision", [None])[0]
                        )
                else:
                    value = {"files": plugin_files.list()}
            elif endpoint == "/plugins/restore":
                value = plugin_files.restore(payload["name"], payload["revision"])
            elif endpoint == "/mod-configs":
                value = {"files": mod_editor.list()}
            elif endpoint == "/mod-config":
                if method == "PUT":
                    value = mod_editor.save(
                        payload["path"], payload["content"], payload["revision"]
                    )
                else:
                    value = mod_editor.read(query["path"][0], query.get("previous") == ["true"])
            elif endpoint == "/backups":
                value = [{"name": "20261113T050000000000Z.tar.gz", "bytes": 12345}]
            elif endpoint.startswith("/users/"):
                kind = endpoint.rsplit("/", 1)[1]
                if method == "PUT":
                    lists[kind] = payload["entries"]
                value = {
                    "entries": lists[kind],
                    "known_players": [{"id": "Steam_76561198000000001", "last_seen": None}],
                }
            elif endpoint == "/actions":
                value = {"id": "browser-job", "state": "queued"}
            elif endpoint == "/logout":
                value = {"ok": True}
            else:
                raise AssertionError(endpoint)
            request.fulfill(json=value)
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            if file.exists():
                request.fulfill(
                    path=str(file),
                    content_type=(
                        "text/html"
                        if file.suffix == ".html"
                        else (
                            "text/css"
                            if file.suffix == ".css"
                            else "font/ttf" if file.suffix == ".ttf" else "text/javascript"
                        )
                    ),
                )
            else:
                request.fulfill(status=404)

    with playwright.sync_playwright() as engine:
        kwargs = {"headless": True}
        if os.environ.get("BROWSER_CHANNEL"):
            kwargs["channel"] = os.environ["BROWSER_CHANNEL"]
        browser = engine.chromium.launch(**kwargs)
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test")
        page.locator("#password").fill("admin-test-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.locator("#dashboard").wait_for(state="visible")
        page.evaluate("document.fonts.ready")
        assert "start the game automatically" in page.locator("#run-intent").inner_text()
        page.evaluate("s => update(s)", state | {"run_intent": {"running": False, "error": None}})
        assert "keep the game stopped" in page.locator("#run-intent").inner_text()
        page.evaluate("s => update(s)", state)
        assert "compatibility" in page.locator("#startup-summary").text_content()
        assert "PlatformInitializer" in page.locator("#startup-history").text_content()
        assert "signal 11" in page.locator("#startup-history").text_content()
        assert "Available on Docker Hub: 0.15.0" in page.locator("#image-version").inner_text()
        assert "New version available" in page.locator("#image-version").inner_text()
        # No disruptive action is submitted before explicit HTML confirmation.
        for action in ("restart", "stop", "update", "backup"):
            before = len(requests)
            trigger = page.locator(f'#actions button[data-action="{action}"]')
            trigger.click()
            dialog = page.get_by_role("dialog")
            assert dialog.is_visible()
            assert "Connected players" in dialog.inner_text()
            assert page.evaluate("document.activeElement.textContent.trim()") == "Cancel"
            assert len(requests) == before
            dialog.get_by_role("button", name="Cancel", exact=True).click()
            assert not dialog.is_visible()
            assert len(requests) == before
            assert trigger.evaluate("element => element === document.activeElement")
            trigger.click()
            page.keyboard.press("Escape")
            assert not dialog.is_visible()
            assert len(requests) == before
            trigger.click()
            page.locator("#action-confirm-submit").click()
            page.wait_for_function("!actionPending")
            assert requests[before:] == [("/actions", {"action": action})]

        assert page.evaluate(
            "document.fonts.check('600 24px Cinzel') && document.fonts.check('400 14px Manrope')"
        )
        assert (
            page.get_by_role("heading", name="Automatic maintenance", include_hidden=True).count()
            == 1
        )
        assert page.get_by_role("tab").count() == 8
        assert page.get_by_role("tabpanel").count() == 1
        assert (
            page.get_by_role("tab", name="Overview", exact=True).get_attribute("aria-selected")
            == "true"
        )
        assert page.locator("#config-form").is_hidden()
        # Arrow keys activate the next tab; Home/End reach either end.
        page.get_by_role("tab", name="Overview", exact=True).focus()
        page.keyboard.press("End")
        assert page.locator("#logs").is_visible()
        assert "14/11/2026" in page.locator("#jobs time").inner_text()
        assert "05:00:16" in page.locator("#jobs time").inner_text()
        assert page.locator("#jobs time").get_attribute("datetime") == "2026-11-14T08:00:16.000Z"
        assert "Restart · completed" in page.locator("#jobs").inner_text()
        assert "Running duration: 10s" in page.locator("#jobs").inner_text()
        page.locator("#history-origin").select_option("schedule")
        assert "No operations" in page.locator("#jobs").inner_text()
        page.locator("#history-origin").select_option("administrator")
        assert "Restart" in page.locator("#jobs").inner_text()
        page.locator("#history-from").fill("2026-11-15")
        page.locator("#history-from").dispatch_event("change")
        assert "No operations" in page.locator("#jobs").inner_text()
        page.locator("#history-from").fill("")
        page.locator("#history-from").dispatch_event("change")
        page.locator("#history-state").select_option("failed")
        assert "No operations" in page.locator("#jobs").inner_text()
        page.locator("#history-state").select_option("")
        assert "Restart" in page.locator("#jobs").inner_text()
        page.locator("#tab-activity").focus()
        page.keyboard.press("ArrowRight")
        assert page.locator("#cpu").is_visible()
        page.keyboard.press("ArrowRight")
        assert page.locator("#config-form").is_visible()
        assert (
            page.get_by_role("tab", name="Game settings", exact=True).get_attribute("tabindex")
            == "0"
        )
        page.get_by_role("tab", name="Server settings", exact=True).click()
        page.get_by_role("button", name="Check for updates", exact=True).click()
        assert "0.15.0" in page.locator("#image-version-status").inner_text()
        assert page.locator("#setting-server_name").is_hidden()
        page.locator("#setting-restart_at").fill("04:00")
        page.get_by_role("tab", name="Game settings", exact=True).click()
        page.locator("#setting-server_name").fill("Mundo de teste")
        page.locator("#setting-modifier_resources").select_option("more")
        page.get_by_role("button", name="Save game settings", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Settings saved')"
        )
        assert requests[-1][1]["changes"] == {
            "server_name": "Mundo de teste",
            "modifier_resources": "more",
        }
        assert runtime.config.effective().restart_at == "off"
        page.get_by_role("tab", name="Server settings", exact=True).click()
        assert page.locator("#setting-restart_at").input_value() == "04:00"
        page.get_by_role("button", name="Save server settings", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#setting-restart_at').value === '04:00' && document.querySelector('#setting-restart_at').closest('.setting').textContent.includes('panel')"
        )
        assert runtime.config.effective().restart_at == "04:00"
        page.get_by_role("tab", name="Mods", exact=True).click()
        assert page.locator("#setting-bepinex_url").count() == 0
        assert page.locator("#setting-bepinex_sha256").count() == 0
        page.locator("#setting-bepinex").select_option("true")
        page.get_by_role("button", name="Save mod settings", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#setting-bepinex').closest('.setting').textContent.includes('panel')"
        )
        assert runtime.config.effective().bepinex
        assert "BepInEx loader completed" in page.locator("#mod-session-status").inner_text()
        assert "Useful Plugin · 1.2.3" in page.locator("#mod-session-plugins").inner_text()
        assert "<unsafe>" in page.locator("#mod-session-issues").inner_text()
        assert page.locator("#mod-session-issues unsafe").count() == 0

        page.locator("#plugin-file").set_input_files(
            {"name": "test.dll", "mimeType": "application/octet-stream", "buffer": dll()}
        )
        page.get_by_role("button", name="Upload plugin", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#plugin-upload-status').textContent.startsWith('Plugin uploaded')"
        )
        assert (plugin_files.root / "test.dll").read_bytes() == dll()
        page.locator("#plugin-file").set_input_files(
            {"name": "test.dll", "mimeType": "application/octet-stream", "buffer": dll(2)}
        )
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Upload plugin", exact=True).click()
        page.wait_for_function("document.querySelector('#plugin-file').value===''")
        assert (plugin_files.root / "test.dll").read_bytes() == dll(2)
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Restore previous version of test.dll", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Previous DLL restored')"
        )
        assert (plugin_files.root / "test.dll").read_bytes() == dll()
        page.locator("#mod-config-file").select_option("test.cfg")
        page.wait_for_function(
            "document.querySelector('#mod-config-content').value==='Enabled = true'"
        )
        page.locator("#mod-config-content").fill("Enabled = false")
        page.get_by_role("button", name="Save file", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('File saved')"
        )
        assert plugin_config.read_text() == "Enabled = false"
        page.get_by_role("button", name="Load previous version", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#mod-config-content').value==='Enabled = true'"
        )
        page.get_by_role("button", name="Save file", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('File saved')"
        )
        assert plugin_config.read_text() == "Enabled = true"
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.get_by_role("button", name="Create backup", exact=True).click()
        page.locator("#action-confirm-submit").click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Operation requested')"
        )
        assert requests[-1] == ("/actions", {"action": "backup"})
        assert page.locator("#players").inner_text() == "2"
        assert "Source: game log (Connections)" in page.locator("#player-reason").inner_text()
        assert "Age: 12s" in page.locator("#player-reason").inner_text()
        unknown = state | {
            "players": {
                "count": None,
                "reason": "Connection changed; waiting for a new snapshot",
                "source": "game_log",
                "last_count": 0,
                "age_seconds": 40,
                "updated_at": 1794643216,
            }
        }
        page.evaluate("value => update(value)", unknown)
        assert page.locator("#players").inner_text() == "?"
        assert (
            "Last count: 0 (not currently confirmed)" in page.locator("#player-reason").inner_text()
        )
        expired = unknown | {
            "players": unknown["players"]
            | {"reason": "Snapshot missing or expired", "age_seconds": 661}
        }
        page.evaluate("value => update(value)", expired)
        assert page.locator("#players").inner_text() == "?"
        assert "expired" in page.locator("#player-reason").inner_text()
        empty = state | {"players": state["players"] | {"count": 0, "idle_wait_seconds": 120}}
        page.evaluate("value => update(value)", empty)
        assert "waiting 120s" in page.locator("#player-reason").inner_text()
        page.evaluate("value => update(value)", state)

        page.get_by_role("tab", name="Players", exact=True).click()
        assert page.locator("#users-form").is_visible()
        page.locator("#known-player").select_option("Steam_76561198000000001")
        page.get_by_role("button", name="Add to list", exact=True).click()
        assert page.locator("#user-list code").count() == 2
        page.get_by_role("button", name="Save list", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('List saved')"
        )
        assert requests[-1] == (
            "/users/admins",
            {"entries": ["Steam_123", "Steam_76561198000000001"]},
        )
        page.get_by_role("button", name="Remove Steam_123", exact=True).click()
        assert page.locator("#user-list code").count() == 1
        page.get_by_role("button", name="Refresh players", exact=True).click()
        assert page.locator("#user-list code").count() == 1
        page.locator("#player-id").fill("76561198000000001")
        page.get_by_role("button", name="Add to list", exact=True).click()
        assert "already" in page.locator("#notice").inner_text()
        assert page.locator("#user-list code").count() == 1
        page.get_by_role("button", name="Save list", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('List saved')"
        )
        assert lists["admins"] == ["Steam_76561198000000001"]
        page.locator("#user-kind").select_option("banned")
        page.wait_for_function(
            "document.querySelector('#user-list-count').textContent.startsWith('0 ')"
        )
        page.locator("#player-id").fill("123456")
        page.get_by_role("button", name="Add to list", exact=True).click()
        assert "complete platform ID" in page.locator("#notice").inner_text()
        assert page.locator("#user-list code").count() == 0
        page.locator("#player-id").fill("Xbox_12345")
        page.get_by_role("button", name="Add to list", exact=True).click()
        page.get_by_role("button", name="Save list", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('List saved')"
        )
        assert lists["banned"] == ["Xbox_12345"]
        player_artifact = Path(__file__).resolve().parents[1] / ".artifacts/panel-players.png"
        player_artifact.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(player_artifact), full_page=True)
        page.get_by_role("tab", name="Backups", exact=True).click()
        page.locator("#cloud-enabled").select_option("true")
        page.locator("#cloud-folder").fill("Valheim/teste")
        page.locator("#cloud-file").set_input_files(
            {
                "name": "valheim-cloud.conf",
                "mimeType": "text/plain",
                "buffer": b'[account]\ntype=dropbox\ntoken={"access_token":"secret","refresh_token":"refresh"}',
            }
        )
        page.wait_for_function(
            "document.querySelector('#cloud-config').value.includes('refresh_token')"
        )
        page.get_by_role("button", name="Save cloud settings", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Cloud upload settings saved')"
        )
        assert runtime.cloud.describe()["enabled"]
        assert page.locator("#cloud-config").input_value() == ""
        assert page.locator("#cloud-file").input_value() == ""
        assert "Dropbox" in page.locator("#cloud-status").inner_text()
        assert page.locator("#crossplay-info").is_visible()
        assert page.locator("#join-code").inner_text() == "012345"
        page.evaluate("s => update(s)", state | {"join_code": None})
        assert "Waiting" in page.locator("#join-code").inner_text()
        page.evaluate("s => update(s)", state | {"join_code": None, "running": False})
        assert "Available when the server starts" in page.locator("#join-code").inner_text()
        page.evaluate("s => update(s)", state | {"crossplay": False, "join_code": None})
        assert page.locator("#crossplay-info").is_hidden()
        page.evaluate("s => update(s)", state)
        assert page.locator("#backup-list a").count() == 1
        artifact = Path(__file__).resolve().parents[1] / ".artifacts/panel.png"
        artifact.parent.mkdir(exist_ok=True)
        page.get_by_role("tab", name="Overview", exact=True).click()
        page.screenshot(path=str(artifact), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.locator("#mobile-section").is_visible()
        for option in page.locator("#mobile-section option").all():
            value = option.get_attribute("value")
            page.locator("#mobile-section").select_option(value)
            assert page.locator("#" + value).get_attribute("aria-selected") == "true"
            assert page.get_by_role("tabpanel").count() == 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.locator("#mobile-section").select_option("tab-players")
        page.screenshot(path=str(artifact.with_name("panel-players-mobile.png")), full_page=True)
        page.locator("#mobile-section").select_option("tab-settings")
        page.screenshot(path=str(artifact.with_name("panel-mobile.png")), full_page=True)
        page.set_viewport_size({"width": 1280, "height": 900})
        page.route(
            "https://panel.test/api/v1/plugins",
            lambda request: request.fulfill(status=422, json={"detail": "Permissions unavailable"}),
        )
        page.evaluate("signedIn()")
        assert page.locator("#dashboard").is_visible()
        assert "Could not load the plugin list" in page.locator("#notice").inner_text()
        assert not errors
        page.get_by_role("tab", name="Game settings", exact=True).click()
        page.get_by_role("tab", name="Server settings", exact=True).click()
        page.locator("#alerts-enabled").select_option("true")
        page.locator("#backup-max-age-hours").fill("48")
        page.locator("#cloud-max-age-hours").fill("72")
        page.locator("#alerts-url").fill("https://discord.com/api/webhooks/123/" + "a" * 40)
        page.get_by_role("button", name="Save alerts", exact=True).click()
        page.wait_for_function("document.querySelector('#alerts-url').value === ''")
        assert runtime.alerts.settings["enabled"]
        assert runtime.alerts.settings["backup_max_age_hours"] == 48
        assert runtime.alerts.settings["cloud_max_age_hours"] == 72
        page.get_by_role("button", name="Send test", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('delivered to Discord')"
        )
        assert not errors
        page.get_by_role("tab", name="Server settings", exact=True).click()
        page.locator("#update-image").click()
        assert page.get_by_role("dialog").is_visible()
        page.get_by_role("dialog").get_by_role("button", name="Cancel", exact=True).click()
        assert not any(endpoint == "/image-update" for endpoint, _ in requests)
        page.locator("#update-image").click()
        page.locator("#action-confirm-submit").click()
        page.wait_for_function(
            "document.querySelector('#image-update-status').textContent.includes('Downloading')"
        )
        assert page.locator("#update-image").is_disabled()
        page.reload()
        page.locator("#password").fill("admin-test-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.get_by_role("tab", name="Server settings", exact=True).click()
        assert "Downloading" in page.locator("#image-update-status").inner_text()
        image_update.update(state="succeeded", message="Update completed.")
        page.evaluate("loadImageUpdateStatus()")
        assert page.locator("#update-image").is_enabled()
        assert "completed" in page.locator("#image-update-status").inner_text()
        assert not errors
        browser.close()


def test_accounts_first_login_operator_view_and_admin_management(runtime, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from app.auth import set_password
    from app.web import create_app

    static = Path(__file__).resolve().parents[1] / "app/static"
    monkeypatch.setenv("PANEL_ORIGIN", "https://panel.test")
    monkeypatch.setattr("app.image_version.published_version", lambda: "0.24.0")
    set_password(runtime.data, "browser-admin-password")
    app = create_app(runtime.data, runtime, lifecycle=False)
    operator = app.state.auth.accounts.create(
        "browser-operator", "operator", "temporary-browser-password"
    )
    client = TestClient(app, base_url="https://panel.test")
    visited = []

    def route(request):
        path = request.request.url.removeprefix("https://panel.test")
        if path.startswith("/api/v1"):
            visited.append(path.split("?")[0])
            headers = {
                k: v
                for k, v in request.request.headers.items()
                if k in ("origin", "x-csrf-token", "content-type", "cookie")
            }
            response = client.request(
                request.request.method,
                path,
                content=request.request.post_data_buffer,
                headers=headers,
            )
            request.fulfill(
                status=response.status_code, body=response.content, headers=dict(response.headers)
            )
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            request.fulfill(
                path=str(file),
                content_type=(
                    "text/html"
                    if file.suffix == ".html"
                    else "text/javascript" if file.suffix == ".js" else "text/css"
                ),
            )

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None, headless=True
        )
        page = browser.new_page()
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test/")
        page.locator("#username").fill("browser-operator")
        page.locator("#password").fill("temporary-browser-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.locator("#password-change").wait_for(state="visible")
        assert page.locator("#dashboard").is_hidden()
        assert not any(
            path in visited for path in ("/api/v1/config", "/api/v1/accounts", "/api/v1/audit")
        )
        page.locator("#current-password").fill("temporary-browser-password")
        page.locator("#new-password").fill("personal-browser-password")
        page.locator("#repeat-password").fill("personal-browser-password")
        page.get_by_role("button", name="Save my password", exact=True).click()
        page.locator("#login").wait_for(state="visible")
        page.locator("#password").fill("personal-browser-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.locator("#dashboard").wait_for(state="visible")
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        assert page.get_by_role("tab").count() == 2
        assert page.get_by_role("tab", name="Administration").count() == 0
        assert page.get_by_role("button", name="Update game", exact=True).count() == 0
        assert "zero players" in page.locator("#action-policy").inner_text()
        page.get_by_role("button", name="Stop", exact=True).click()
        assert "will wait" in page.locator("#action-confirm-description").inner_text()
        page.locator("#action-confirm-submit").click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Operation requested')"
        )
        assert runtime.jobs[-1]["actor"]["id"] == operator["id"]
        assert runtime.jobs[-1]["requires_idle"]
        page.get_by_role("tab", name="Activity", exact=True).click()
        assert page.locator("#logs").is_hidden()
        page.locator("#logout").click()
        page.locator("#username").fill("admin")
        page.locator("#password").fill("browser-admin-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        page.get_by_role("tab", name="Administration", exact=True).click()
        page.locator("#account-username").fill("browser-helper")
        page.locator("#account-password").fill("helper-temporary-password")
        page.get_by_role("button", name="Create account", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#account-list').textContent.includes('browser-helper')"
        )
        assert app.state.auth.accounts.find("browser-helper")["must_change_password"]
        assert "helper-temporary-password" not in page.locator("#audit-list").inner_text()
        page.locator("#audit-user").fill("admin")
        page.locator("#audit-operation").select_option("accounts")
        page.get_by_role("button", name="Filter audit log", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#audit-list').textContent.includes('Accounts')"
        )
        assert "admin" in page.locator("#audit-list").inner_text()
        browser.close()


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_touch_navigation_forms_metrics_and_confirmation(runtime, monkeypatch, role):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from app.auth import set_password
    from app.web import create_app

    static = Path(__file__).resolve().parents[1] / "app/static"
    monkeypatch.setenv("PANEL_ORIGIN", "https://panel.test")
    monkeypatch.setattr("app.image_version.published_version", lambda: "0.24.0")
    set_password(runtime.data, "mobile-panel-password")
    app = create_app(runtime.data, runtime, lifecycle=False)
    if role == "operator":
        app.state.auth.accounts.create("mobile-operator", "operator", "mobile-panel-password")
    metric = {
        "time": time.time(),
        "cpu_percent": 33,
        "memory_bytes": 3 * 1024**3,
        "memory_limit": 10 * 1024**3,
        "network_in_bps": 2048,
        "network_out_bps": 4096,
        "disk_used": 8 * 1024**3,
        "disk_total": 50 * 1024**3,
        "scope": "container",
    }
    app.state.metrics.history.extend([metric | {"time": metric["time"] - 10}, metric])
    client = TestClient(app, base_url="https://panel.test")
    errors = []
    actions = []

    def route(request):
        path = request.request.url.removeprefix("https://panel.test")
        if path.startswith("/api/v1"):
            if path == "/api/v1/actions":
                actions.append(request.request.post_data_json)
            headers = {
                k: v
                for k, v in request.request.headers.items()
                if k in ("origin", "x-csrf-token", "content-type", "cookie")
            }
            result = client.request(
                request.request.method,
                path,
                content=request.request.post_data_buffer,
                headers=headers,
            )
            request.fulfill(
                status=result.status_code, body=result.content, headers=dict(result.headers)
            )
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            request.fulfill(
                path=str(file),
                content_type=(
                    "text/html"
                    if file.suffix == ".html"
                    else (
                        "text/javascript"
                        if file.suffix == ".js"
                        else "text/css" if file.suffix == ".css" else "font/ttf"
                    )
                ),
            )

    def no_horizontal_scroll(page):
        assert page.evaluate(
            "document.documentElement.scrollWidth <= window.innerWidth"
        ), page.evaluate("({width:innerWidth,scroll:document.documentElement.scrollWidth})")

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None, headless=True
        )
        context = browser.new_context(
            viewport={"width": 320, "height": 740},
            is_mobile=True,
            has_touch=True,
            device_scale_factor=2,
        )
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test/")
        no_horizontal_scroll(page)
        assert (
            float(
                page.locator("#username")
                .evaluate("element => getComputedStyle(element).fontSize")
                .removesuffix("px")
            )
            >= 16
        )
        page.locator("#username").fill("admin" if role == "admin" else "mobile-operator")
        page.locator("#password").fill("mobile-panel-password")
        page.get_by_role("button", name="Sign in", exact=True).tap()
        if role == "operator":
            page.locator("#password-change").wait_for(state="visible")
            assert page.locator("#dashboard").is_hidden()
            page.set_viewport_size({"width": 320, "height": 520})
            page.locator("#current-password").fill("mobile-panel-password")
            page.locator("#new-password").fill("mobile-personal-password")
            page.locator("#repeat-password").fill("mobile-personal-password")
            no_horizontal_scroll(page)
            page.get_by_role("button", name="Save my password", exact=True).tap()
            page.locator("#login").wait_for(state="visible")
            page.locator("#password").fill("mobile-personal-password")
            page.get_by_role("button", name="Sign in", exact=True).tap()
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        for width, height in [(320, 740), (390, 844), (430, 932), (768, 1024), (844, 390)]:
            page.set_viewport_size({"width": width, "height": height})
            mobile = width <= 760 or height <= 500
            if mobile:
                assert page.locator("#mobile-section").is_visible()
                assert page.locator("#mobile-section option").count() == (
                    8 if role == "admin" else 2
                )
                sections = [
                    option.get_attribute("value")
                    for option in page.locator("#mobile-section option").all()
                ]
                for section in sections:
                    page.locator("#mobile-section").select_option(section)
                    assert page.get_by_role("tabpanel").count() == 1
                    no_horizontal_scroll(page)
                    for button in page.locator("button:visible:not(:disabled)").all():
                        assert button.bounding_box()["height"] >= 43
            else:
                for tab in page.get_by_role("tab").all():
                    tab.tap()
                    no_horizontal_scroll(page)
            if mobile:
                page.locator("#mobile-section").select_option("tab-overview")
            else:
                page.get_by_role("tab", name="Overview", exact=True).tap()
            page.wait_for_timeout(50)
            assert page.locator("#cpu-chart").evaluate(
                "c => Math.abs(c.width - c.getBoundingClientRect().width * devicePixelRatio) < 2"
            )
            page.get_by_role("button", name="Restart", exact=True).tap()
            assert page.locator("#action-confirmation").is_visible()
            no_horizontal_scroll(page)
            page.get_by_role("button", name="Cancel", exact=True).tap()
            assert actions == []
        page.set_viewport_size({"width": 390, "height": 844})
        page.locator("#mobile-section").select_option("tab-overview")
        page.evaluate("scrollTo(0,0)")
        artifact = (
            Path(__file__).resolve().parents[1] / ".artifacts" / ("panel-phone-" + role + ".png")
        )
        artifact.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(artifact))
        page.get_by_role("button", name="Restart", exact=True).tap()
        page.locator("#action-confirm-submit").tap()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Operation requested')"
        )
        assert actions == [{"action": "restart"}]
        assert bool(runtime.jobs[-1].get("requires_idle")) == (role == "operator")
        if role == "admin":
            page.locator("#mobile-section").select_option("tab-administration")
            page.locator("#account-username").fill("mobile-helper")
            page.locator("#account-password").fill("helper-temporary-password")
            page.get_by_role("button", name="Create account", exact=True).tap()
            page.wait_for_function(
                "document.querySelector('#account-list').textContent.includes('mobile-helper')"
            )
            no_horizontal_scroll(page)
        assert not errors
        context.close()
        browser.close()


def test_backup_age_status_and_configuration_on_phone(runtime, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from app.auth import set_password
    from app.web import create_app

    static = Path(__file__).resolve().parents[1] / "app/static"
    monkeypatch.setenv("PANEL_ORIGIN", "https://panel.test")
    monkeypatch.setattr("app.image_version.published_version", lambda: "0.25.0")
    monkeypatch.setattr(
        runtime.backups,
        "latest_completed",
        lambda now: {"time": now - 7200, "name": "Completed.tar.gz"},
    )
    runtime.alerts.configure(False, backup_max_age_hours=1, cloud_max_age_hours=0)
    set_password(runtime.data, "freshness-admin-password")
    client = TestClient(
        create_app(runtime.data, runtime, lifecycle=False), base_url="https://panel.test"
    )

    def route(request):
        path = request.request.url.removeprefix("https://panel.test")
        if path.startswith("/api/v1"):
            response = client.request(
                request.request.method,
                path,
                content=request.request.post_data_buffer,
                headers={
                    k: v
                    for k, v in request.request.headers.items()
                    if k in ("origin", "x-csrf-token", "content-type", "cookie")
                },
            )
            request.fulfill(
                status=response.status_code, body=response.content, headers=dict(response.headers)
            )
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            request.fulfill(
                path=str(file),
                content_type={
                    ".html": "text/html",
                    ".js": "text/javascript",
                    ".css": "text/css",
                }.get(file.suffix, "font/ttf"),
            )

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None, headless=True
        )
        page = browser.new_page(
            viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True
        )
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test/")
        page.locator("#password").fill("freshness-admin-password")
        page.get_by_role("button", name="Sign in", exact=True).tap()
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        assert page.locator("#backup-age-warning").is_visible()
        assert "Local backup" in page.locator("#backup-age-warning").inner_text()
        page.locator("#mobile-section").select_option("tab-backups")
        assert "Last success" in page.locator("#local-backup-freshness").inner_text()
        assert "Overdue" in page.locator("#local-backup-freshness").inner_text()
        assert "Cloud uploads disabled" in page.locator("#cloud-backup-freshness").inner_text()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.locator("#mobile-section").select_option("tab-server")
        page.locator("#backup-max-age-hours").fill("0")
        page.locator("#cloud-max-age-hours").fill("48")
        page.get_by_role("button", name="Save alerts", exact=True).tap()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Alerts saved')"
        )
        assert not runtime.alerts.describe()["active"]
        assert not runtime.alerts.settings["enabled"]
        assert page.locator("#backup-age-warning").is_hidden()
        page.locator("#mobile-section").select_option("tab-backups")
        assert "Deadline disabled" in page.locator("#local-backup-freshness").inner_text()
        page.locator("#logout").tap()
        assert page.locator("#local-backup-freshness").text_content() == ""
        browser.close()


def test_player_identification_confirmation_on_phone(runtime, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from app.auth import set_password
    from app.web import create_app

    static = Path(__file__).resolve().parents[1] / "app/static"
    monkeypatch.setenv("PANEL_ORIGIN", "https://panel.test")
    monkeypatch.setattr("app.image_version.published_version", lambda: "0.25.0")
    set_password(runtime.data, "identity-admin-password")
    runtime.player_directory.consume(
        "PlayFab socket with remote ID playfab/ABC123 received local Platform ID Xbox_12345"
    )
    client = TestClient(
        create_app(runtime.data, runtime, lifecycle=False), base_url="https://panel.test"
    )

    def route(request):
        path = request.request.url.removeprefix("https://panel.test")
        if path.startswith("/api/v1"):
            response = client.request(
                request.request.method,
                path,
                content=request.request.post_data_buffer,
                headers={
                    k: v
                    for k, v in request.request.headers.items()
                    if k in ("origin", "x-csrf-token", "content-type", "cookie")
                },
            )
            request.fulfill(
                status=response.status_code, body=response.content, headers=dict(response.headers)
            )
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            mime = {".html": "text/html", ".js": "text/javascript", ".css": "text/css"}.get(
                file.suffix, "font/ttf"
            )
            request.fulfill(path=str(file), content_type=mime)

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None, headless=True
        )
        page = browser.new_page(
            viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True
        )
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test/")
        page.locator("#password").fill("identity-admin-password")
        page.get_by_role("button", name="Sign in", exact=True).tap()
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        page.locator("#mobile-section").select_option("tab-players")
        page.locator("#known-player").select_option("Xbox_12345")
        assert page.locator("#player-id").input_value() == ""
        assert page.locator("#player-identity-form").is_visible()
        assert not page.locator("#directory-confirm").is_checked()
        # The F2 permission ID may differ from the transport ID: preserve the corrected value.
        page.locator("#directory-id").fill("X_987654321")
        page.locator("#directory-platform").fill("Xbox")
        page.locator("#directory-name").fill("Jogador <img src=x onerror=alert(1)>")
        page.get_by_role("button", name="Save identity", exact=True).tap()
        assert all(item["id"] != "X_987654321" for item in runtime.player_directory.describe())
        page.locator("#directory-confirm").check()
        page.get_by_role("button", name="Save identity", exact=True).tap()
        page.wait_for_function("document.querySelector('#player-id').value === 'X_987654321'")
        assert client.get("/api/v1/users/admins").json()["entries"] == []
        page.get_by_role("button", name="Add to list", exact=True).tap()
        assert "Jogador <img" in page.locator("#user-list").inner_text()
        assert page.locator("#user-list img").count() == 0
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        # Adding to the draft still must not grant permissions before saving the list.
        assert client.get("/api/v1/users/admins").json()["entries"] == []
        page.locator("#logout").tap()
        assert page.locator("#directory-id").input_value() == ""
        assert page.locator("#directory-name").input_value() == ""
        assert page.locator("#directory-platform").input_value() == ""
        browser.close()


def test_plugin_delete_confirmation_and_restore_on_phone(runtime, monkeypatch):
    playwright = pytest.importorskip("playwright.sync_api")
    from fastapi.testclient import TestClient
    from app.auth import set_password
    from app.web import create_app
    from tests.plugin_fixture import dll

    static = Path(__file__).resolve().parents[1] / "app/static"
    monkeypatch.setenv("PANEL_ORIGIN", "https://panel.test")
    monkeypatch.setattr("app.image_version.published_version", lambda: "0.24.0")
    set_password(runtime.data, "plugin-admin-password")
    plugin = runtime.data / "mods/BepInEx/plugins/Author-Plugin/example.dll"
    plugin.parent.mkdir(parents=True)
    plugin.write_bytes(dll())
    config = runtime.data / "mods/BepInEx/config/example.cfg"
    config.parent.mkdir(parents=True)
    config.write_text("custom-config")
    app = create_app(runtime.data, runtime, lifecycle=False)
    client = TestClient(app, base_url="https://panel.test")
    writes = []

    def route(request):
        path = request.request.url.removeprefix("https://panel.test")
        if path.startswith("/api/v1"):
            if request.request.method == "POST" and path.startswith("/api/v1/plugins/"):
                writes.append(path)
            headers = {
                k: v
                for k, v in request.request.headers.items()
                if k in ("origin", "x-csrf-token", "content-type", "cookie")
            }
            result = client.request(
                request.request.method,
                path,
                content=request.request.post_data_buffer,
                headers=headers,
            )
            request.fulfill(
                status=result.status_code, body=result.content, headers=dict(result.headers)
            )
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            request.fulfill(
                path=str(file),
                content_type=(
                    "text/html"
                    if file.suffix == ".html"
                    else (
                        "text/javascript"
                        if file.suffix == ".js"
                        else "text/css" if file.suffix == ".css" else "font/ttf"
                    )
                ),
            )

    with playwright.sync_playwright() as p:
        browser = p.chromium.launch(
            channel=os.environ.get("BROWSER_CHANNEL") or None, headless=True
        )
        context = browser.new_context(
            viewport={"width": 320, "height": 640}, is_mobile=True, has_touch=True
        )
        page = context.new_page()
        page.add_init_script("window.EventSource = class { close() {} };")
        page.route("https://panel.test/**", route)
        page.goto("https://panel.test/")
        page.locator("#password").fill("plugin-admin-password")
        page.get_by_role("button", name="Sign in", exact=True).tap()
        page.wait_for_function(
            "document.querySelector('#panel-health').textContent.includes('connected')"
        )
        page.locator("#mobile-section").select_option("tab-mods")
        button = page.get_by_role(
            "button", name="Delete plugin Author-Plugin/example.dll", exact=True
        )
        assert button.is_enabled()
        button.tap()
        assert (
            "Author-Plugin/example.dll" in page.locator("#plugin-confirm-description").inner_text()
        )
        page.get_by_role("button", name="Cancel", exact=True).tap()
        assert plugin.exists() and not writes
        button.tap()
        page.locator("#plugin-confirm-submit").tap()
        page.wait_for_function(
            "document.querySelector('#deleted-plugin-list').textContent.includes('example.dll')"
        )
        assert not plugin.exists()
        assert config.read_text() == "custom-config"
        restore = page.get_by_role(
            "button", name="Restore deleted plugin Author-Plugin/example.dll", exact=True
        )
        restore.tap()
        page.locator("#plugin-confirm-submit").tap()
        page.wait_for_function(
            "document.querySelector('#plugin-list').textContent.includes('example.dll')"
        )
        assert plugin.read_bytes() == dll()
        assert config.read_text() == "custom-config"
        assert writes == ["/api/v1/plugins/delete", "/api/v1/plugins/restore-deleted"]
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        browser.close()
