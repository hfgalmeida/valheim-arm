"""Optional real-browser UI check with deterministic API fixtures, no game."""

import math
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_BROWSER_TESTS") != "1",
    reason="Ative RUN_BROWSER_TESTS=1 para o teste de navegador",
)


def test_panel_login_forms_actions_and_mobile(runtime):
    playwright = pytest.importorskip("playwright.sync_api")
    static = Path(__file__).resolve().parents[1] / "app/static"
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
        "scope": "contêiner (cgroup v2)",
    }
    state = runtime.status()
    state.update(
        state="online",
        running=True,
        crossplay=True,
        join_code="012345",
        players={"count": 2, "reason": "snapshot do jogo"},
        installed_build="123456",
        available_build="123456",
        jobs=[
            {
                "action": "restart",
                "state": "concluído",
                "created": 1794643216,
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
                request.fulfill(status=401, json={"detail": "Faça login"})
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
            elif endpoint == "/state":
                value = state
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
                value = {"id": "browser-job", "state": "fila"}
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
        page.get_by_role("button", name="Entrar", exact=True).click()
        page.locator("#dashboard").wait_for(state="visible")
        page.evaluate("document.fonts.ready")
        assert page.evaluate(
            "document.fonts.check('600 24px Cinzel') && document.fonts.check('400 14px Manrope')"
        )
        assert (
            page.get_by_role("heading", name="Rotina automática", include_hidden=True).count() == 1
        )
        assert page.get_by_role("tab").count() == 5
        assert page.get_by_role("tabpanel").count() == 1
        assert (
            page.get_by_role("tab", name="Visão geral", exact=True).get_attribute("aria-selected")
            == "true"
        )
        assert page.locator("#config-form").is_hidden()
        # Arrow keys activate the next tab; Home/End reach either end.
        page.get_by_role("tab", name="Visão geral", exact=True).focus()
        page.keyboard.press("End")
        assert page.locator("#logs").is_visible()
        assert "14/11/2026" in page.locator("#jobs time").inner_text()
        assert "05:00:16" in page.locator("#jobs time").inner_text()
        assert page.locator("#jobs time").get_attribute("datetime") == "2026-11-14T08:00:16.000Z"
        assert "restart · concluído" in page.locator("#jobs").inner_text()
        page.keyboard.press("ArrowRight")
        assert page.locator("#cpu").is_visible()
        page.keyboard.press("ArrowRight")
        assert page.locator("#config-form").is_visible()
        assert (
            page.get_by_role("tab", name="Configuração", exact=True).get_attribute("tabindex")
            == "0"
        )
        page.locator("#setting-server_name").fill("Mundo de teste")
        page.locator("#setting-modifier_resources").select_option("more")
        page.get_by_role("button", name="Salvar configuração", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Configuração salva')"
        )
        assert requests[-1][1]["changes"] == {
            "server_name": "Mundo de teste",
            "modifier_resources": "more",
        }
        page.locator("#plugin-file").set_input_files(
            {"name": "test.dll", "mimeType": "application/octet-stream", "buffer": dll()}
        )
        page.get_by_role("button", name="Enviar plugin", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#plugin-upload-status').textContent.startsWith('Plugin enviado')"
        )
        assert (plugin_files.root / "test.dll").read_bytes() == dll()
        page.locator("#plugin-file").set_input_files(
            {"name": "test.dll", "mimeType": "application/octet-stream", "buffer": dll(2)}
        )
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Enviar plugin", exact=True).click()
        page.wait_for_function("document.querySelector('#plugin-file').value===''")
        assert (plugin_files.root / "test.dll").read_bytes() == dll(2)
        page.once("dialog", lambda dialog: dialog.accept())
        page.get_by_role("button", name="Restaurar versão anterior de test.dll", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('DLL anterior restaurada')"
        )
        assert (plugin_files.root / "test.dll").read_bytes() == dll()
        page.locator("#mod-config-file").select_option("test.cfg")
        page.wait_for_function(
            "document.querySelector('#mod-config-content').value==='Enabled = true'"
        )
        page.locator("#mod-config-content").fill("Enabled = false")
        page.get_by_role("button", name="Salvar arquivo", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Arquivo salvo')"
        )
        assert plugin_config.read_text() == "Enabled = false"
        page.get_by_role("button", name="Carregar versão anterior", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#mod-config-content').value==='Enabled = true'"
        )
        page.get_by_role("button", name="Salvar arquivo", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Arquivo salvo')"
        )
        assert plugin_config.read_text() == "Enabled = true"
        page.get_by_role("tab", name="Visão geral", exact=True).click()
        page.get_by_role("button", name="Criar backup", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Operação enviada')"
        )
        assert requests[-1] == ("/actions", {"action": "backup"})
        assert page.locator("#players").inner_text() == "2"
        page.get_by_role("tab", name="Jogadores", exact=True).click()
        assert page.locator("#users-form").is_visible()
        page.locator("#known-player").select_option("Steam_76561198000000001")
        page.get_by_role("button", name="Adicionar à lista", exact=True).click()
        assert page.locator("#user-list code").count() == 2
        page.get_by_role("button", name="Salvar lista", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Lista salva')"
        )
        assert requests[-1] == (
            "/users/admins",
            {"entries": ["Steam_123", "Steam_76561198000000001"]},
        )
        page.get_by_role("button", name="Remover Steam_123", exact=True).click()
        assert page.locator("#user-list code").count() == 1
        page.get_by_role("button", name="Atualizar jogadores", exact=True).click()
        assert page.locator("#user-list code").count() == 1
        page.locator("#player-id").fill("76561198000000001")
        page.get_by_role("button", name="Adicionar à lista", exact=True).click()
        assert "já está" in page.locator("#notice").inner_text()
        assert page.locator("#user-list code").count() == 1
        page.get_by_role("button", name="Salvar lista", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Lista salva')"
        )
        assert lists["admins"] == ["Steam_76561198000000001"]
        page.locator("#user-kind").select_option("banned")
        page.wait_for_function(
            "document.querySelector('#user-list-count').textContent.startsWith('0 ')"
        )
        page.locator("#player-id").fill("123456")
        page.get_by_role("button", name="Adicionar à lista", exact=True).click()
        assert "ID de plataforma completo" in page.locator("#notice").inner_text()
        assert page.locator("#user-list code").count() == 0
        page.locator("#player-id").fill("Xbox_12345")
        page.get_by_role("button", name="Adicionar à lista", exact=True).click()
        page.get_by_role("button", name="Salvar lista", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.startsWith('Lista salva')"
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
        page.get_by_role("button", name="Salvar envio na nuvem", exact=True).click()
        page.wait_for_function(
            "document.querySelector('#notice').textContent.includes('Envio na nuvem salvo')"
        )
        assert runtime.cloud.describe()["enabled"]
        assert page.locator("#cloud-config").input_value() == ""
        assert page.locator("#cloud-file").input_value() == ""
        assert "Dropbox" in page.locator("#cloud-status").inner_text()
        assert page.locator("#crossplay-info").is_visible()
        assert page.locator("#join-code").inner_text() == "012345"
        page.evaluate("s => update(s)", state | {"join_code": None})
        assert "Aguardando" in page.locator("#join-code").inner_text()
        page.evaluate("s => update(s)", state | {"join_code": None, "running": False})
        assert "Disponível ao iniciar" in page.locator("#join-code").inner_text()
        page.evaluate("s => update(s)", state | {"crossplay": False, "join_code": None})
        assert page.locator("#crossplay-info").is_hidden()
        page.evaluate("s => update(s)", state)
        assert page.locator("#backup-list a").count() == 1
        artifact = Path(__file__).resolve().parents[1] / ".artifacts/panel.png"
        artifact.parent.mkdir(exist_ok=True)
        page.get_by_role("tab", name="Visão geral", exact=True).click()
        page.screenshot(path=str(artifact), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        for tab in page.get_by_role("tab").all():
            tab.click()
            assert tab.get_attribute("aria-selected") == "true"
            assert page.get_by_role("tabpanel").count() == 1
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.get_by_role("tab", name="Jogadores", exact=True).click()
        page.screenshot(path=str(artifact.with_name("panel-players-mobile.png")), full_page=True)
        page.get_by_role("tab", name="Configuração", exact=True).click()
        page.screenshot(path=str(artifact.with_name("panel-mobile.png")), full_page=True)
        page.route(
            "https://panel.test/api/v1/plugins",
            lambda request: request.fulfill(
                status=422, json={"detail": "Permissões indisponíveis"}
            ),
        )
        page.evaluate("signedIn()")
        assert page.locator("#dashboard").is_visible()
        assert (
            "Não foi possível carregar a lista de plugins" in page.locator("#notice").inner_text()
        )
        assert not errors
        browser.close()
