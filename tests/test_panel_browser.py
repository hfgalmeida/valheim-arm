"""Optional real-browser UI check with deterministic API fixtures, no game."""
import json
import math
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("RUN_BROWSER_TESTS") != "1", reason="Ative RUN_BROWSER_TESTS=1 para o teste de navegador")


def test_panel_login_forms_actions_and_mobile(runtime):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).resolve().parents[1] / "app/static"
    authenticated = False
    requests = []
    metric = {"time": time.time(), "cpu_percent": 72, "memory_bytes": 3 * 1024**3,
              "memory_limit": 10 * 1024**3, "network_in_bps": 2048, "network_out_bps": 4096,
              "disk_used": 8 * 1024**3, "disk_total": 50 * 1024**3, "scope": "contêiner (cgroup v2)"}
    state = runtime.status()
    state.update(state="online", running=True, players={"count": 2, "reason": "snapshot do jogo"},
                 installed_build="123456", available_build="123456",
                 metrics=[metric | {"time": metric["time"] - (29-i)*5, "cpu_percent": 50+30*math.sin(i/4)} for i in range(30)])

    def route(request):
        nonlocal authenticated
        path = request.request.url.removeprefix("https://panel.test")
        method = request.request.method
        if path.startswith("/api/v1"):
            endpoint = path.removeprefix("/api/v1")
            if endpoint == "/session":
                request.fulfill(status=401, json={"detail": "Faça login"})
                return
            if endpoint == "/login":
                authenticated = True
                request.fulfill(json={"csrf": "browser-csrf"})
                return
            assert authenticated
            if method in ("POST", "PATCH", "PUT"):
                payload = request.request.post_data_json
                requests.append((endpoint, payload))
            if endpoint == "/config":
                if method == "PATCH":
                    runtime.config.change(payload.get("changes", {}), payload.get("reset", []), payload.get("confirm_world", False))
                value = runtime.config.describe()
            elif endpoint == "/state":
                value = state
            elif endpoint == "/backups":
                value = [{"name": "20261113T050000000000Z.tar.gz", "bytes": 12345}]
            elif endpoint.startswith("/users/"):
                value = {"entries": ["Steam_123"]}
            elif endpoint == "/actions":
                value = {"id": "browser-job", "state": "fila"}
            elif endpoint == "/logout":
                value = {"ok": True}
            else:
                raise AssertionError(endpoint)
            request.fulfill(json=value)
        else:
            file = static / ("index.html" if path == "/" else path.lstrip("/"))
            if file.exists():
                request.fulfill(path=str(file), content_type="text/html" if file.suffix == ".html" else "text/css" if file.suffix == ".css" else "text/javascript")
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
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.locator("#dashboard").wait_for(state="visible")
        page.locator("#setting-server_name").fill("Mundo de teste")
        page.locator("#setting-modifier_resources").select_option("more")
        page.get_by_role("button", name="Salvar configuração", exact=True).click()
        page.wait_for_function("document.querySelector('#notice').textContent.includes('Configuração salva')")
        assert requests[-1][1]["changes"] == {"server_name": "Mundo de teste", "modifier_resources": "more"}
        page.get_by_role("button", name="Criar backup", exact=True).click()
        page.wait_for_function("document.querySelector('#notice').textContent.includes('Operação enviada')")
        assert requests[-1] == ("/actions", {"action": "backup"})
        assert page.locator("#players").inner_text() == "2"
        assert page.locator("#backup-list a").count() == 1
        artifact = Path(__file__).resolve().parents[1] / ".artifacts/panel.png"
        artifact.parent.mkdir(exist_ok=True)
        page.screenshot(path=str(artifact), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        assert not errors
        browser.close()
