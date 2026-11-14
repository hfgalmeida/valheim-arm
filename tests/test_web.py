from fastapi.testclient import TestClient

from app.auth import set_password
from app.web import create_app


def client_fixture(runtime, monkeypatch):
    monkeypatch.setenv("PANEL_ORIGIN", "https://server.test")
    set_password(runtime.data, "admin-test-password")
    app = create_app(runtime.data, runtime, lifecycle=False)
    client = TestClient(app, base_url="https://server.test")
    response = client.post(
        "/api/v1/login",
        json={"password": "admin-test-password"},
        headers={"Origin": "https://server.test"},
    )
    assert response.status_code == 200
    client.headers.update(
        {"Origin": "https://server.test", "X-CSRF-Token": response.json()["csrf"]}
    )
    return client


def test_auth_csrf_and_password_rotation(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    assert client.get("/api/v1/state").status_code == 200
    assert next(c for c in client.cookies.jar if c.name == "session").secure
    assert (
        client.patch(
            "/api/v1/config", json={"changes": {"public": False}}, headers={"X-CSRF-Token": "bad"}
        ).status_code
        == 403
    )
    assert (
        client.patch(
            "/api/v1/config",
            json={"changes": {"public": False}},
            headers={"Origin": "https://evil.test"},
        ).status_code
        == 403
    )
    set_password(runtime.data, "new-admin-password")
    assert client.get("/api/v1/state").status_code == 401


def test_cloud_settings_require_auth_csrf_and_never_return_tokens(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    body = {
        "enabled": True,
        "folder": "Valheim/backups",
        "config": '[account]\ntype=dropbox\ntoken={"access_token":"secret-access","refresh_token":"secret-refresh"}',
    }
    assert (
        client.put("/api/v1/cloud", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
    )
    response = client.put("/api/v1/cloud", json=body)
    assert response.status_code == 200
    assert response.json()["provider"] == "Dropbox"
    for path in ("/api/v1/cloud", "/api/v1/state"):
        response = client.get(path)
        assert "secret-" not in response.text
    bad = client.put("/api/v1/cloud", json=body | {"config": "secret-access"})
    assert bad.status_code == 422
    assert "secret-access" not in bad.text
    bad = client.put("/api/v1/cloud", json=body | {"config": "secret-access" * 7000})
    assert bad.status_code == 422
    assert "secret-access" not in bad.text
    client.cookies.clear()
    assert client.get("/api/v1/cloud").status_code == 401
    assert client.put("/api/v1/cloud", json=body).status_code == 401


def test_config_secrets_and_world_confirmation(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    assert "game-password" not in client.get("/api/v1/config").text
    response = client.patch("/api/v1/config", json={"changes": {"world_name": "New"}})
    assert response.status_code == 422
    assert (
        client.patch(
            "/api/v1/config", json={"changes": {"world_name": "New"}, "confirm_world": True}
        ).status_code
        == 200
    )
    response = client.patch("/api/v1/config", json={"changes": {"server_password": "bad"}})
    assert response.status_code == 422
    assert "input_value" not in response.text


def test_users_and_job_queue(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    assert client.put(
        "/api/v1/users/admins", json={"entries": ["Steam_123", "Steam_123"]}
    ).json() == {"entries": ["Steam_123"]}
    assert client.get("/api/v1/users/admins").json()["entries"] == ["Steam_123"]
    assert client.put("/api/v1/users/admins", json={"entries": ["id\nINJECT"]}).status_code == 422
    response = client.post("/api/v1/actions", json={"action": "backup"})
    assert response.status_code == 202
    job = response.json()
    assert client.get("/api/v1/jobs/" + job["id"]).json()["state"] == "fila"
    assert (
        client.post("/api/v1/actions", json={"action": "restore", "backup": "x.tar.gz"}).status_code
        == 422
    )
    assert client.get("/").status_code == 200
    assert client.get("/panel.js").status_code == 200


def test_login_origin_and_rate_limit(runtime, monkeypatch):
    monkeypatch.setenv("PANEL_ORIGIN", "https://server.test")
    app = create_app(runtime.data, runtime, lifecycle=False)
    client = TestClient(app, base_url="https://server.test")
    assert client.post("/api/v1/login", json={"password": "bad"}).status_code == 403
    for _ in range(5):
        assert (
            client.post(
                "/api/v1/login", json={"password": "bad"}, headers={"Origin": "https://server.test"}
            ).status_code
            == 401
        )
    response = client.post(
        "/api/v1/login", json={"password": "bad"}, headers={"Origin": "https://server.test"}
    )
    assert "Muitas tentativas" in response.text
    assert client.get("/api/v1/backups").status_code == 401


def test_permission_picker_merges_known_ids_and_existing_lists(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    runtime.player_directory.consume("Got connection SteamID 76561198000000001")
    client.put("/api/v1/users/admins", json={"entries": ["Steam_76561198000000001"]})
    client.put("/api/v1/users/permitted", json={"entries": ["Xbox_12345"]})
    response = client.get("/api/v1/users/banned")
    assert response.status_code == 200
    assert response.json()["entries"] == []
    known = response.json()["known_players"]
    assert {entry["id"] for entry in known} == {"Steam_76561198000000001", "Xbox_12345"}
    assert len(known) == 2
    assert "online" not in known[0]
    client.cookies.clear()
    assert client.get("/api/v1/users/admins").status_code == 401


def test_mod_editor_requires_auth_csrf_and_stopped_game(runtime, monkeypatch):
    from types import SimpleNamespace

    client = client_fixture(runtime, monkeypatch)
    path = runtime.data / "mods/BepInEx/config/test.cfg"
    path.parent.mkdir(parents=True)
    path.write_text("Enabled = true")
    assert client.get("/api/v1/mod-configs").json() == {"files": ["test.cfg"]}
    current = client.get("/api/v1/mod-config", params={"path": "test.cfg"}).json()
    body = {"path": "test.cfg", "content": "Enabled = false", "revision": current["revision"]}
    assert (
        client.put("/api/v1/mod-config", json=body, headers={"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    runtime.process = SimpleNamespace(returncode=None)
    assert client.put("/api/v1/mod-config", json=body).status_code == 409
    assert path.read_text() == "Enabled = true"
    runtime.process.returncode = 0
    assert client.put("/api/v1/mod-config", json=body).status_code == 200
    assert client.put("/api/v1/mod-config", json=body).status_code == 409
    assert (
        client.get("/api/v1/mod-config", params={"path": "test.cfg", "previous": True}).json()[
            "content"
        ]
        == "Enabled = true"
    )
    assert client.get("/api/v1/mod-config", params={"path": "../admin.json"}).status_code == 422
    client.cookies.clear()
    assert client.get("/api/v1/mod-configs").status_code == 401
    assert client.get("/api/v1/mod-config", params={"path": "test.cfg"}).status_code == 401


def test_plugin_upload_auth_state_replacement_and_limits(runtime, monkeypatch):
    from types import SimpleNamespace
    from tests.plugin_fixture import dll

    client = client_fixture(runtime, monkeypatch)
    url = "/api/v1/plugins?name=test.dll"
    headers = {"Content-Type": "application/octet-stream"}
    runtime.process = SimpleNamespace(returncode=None)
    assert client.post(url, content=dll(), headers=headers).status_code == 409
    runtime.process.returncode = 0
    assert (
        client.post(url, content=dll(), headers=headers | {"X-CSRF-Token": "wrong"}).status_code
        == 403
    )
    assert client.post(url, content=b"not DLL", headers=headers).status_code == 422
    response = client.post(url, content=dll(), headers=headers)
    assert response.status_code == 200
    first = response.json()
    assert client.post(url, content=dll(2), headers=headers).status_code == 409
    response = client.post(url + "&revision=" + first["revision"], content=dll(2), headers=headers)
    assert response.status_code == 200 and response.json()["previous_available"]
    restored = client.post(
        "/api/v1/plugins/restore",
        json={"name": "test.dll", "revision": response.json()["revision"]},
    )
    assert restored.status_code == 200 and restored.json()["revision"] == first["revision"]
    with monkeypatch.context() as limited:
        limited.setattr("app.web.MAX_PLUGIN_SIZE", 512)
        assert (
            client.post(
                "/api/v1/plugins?name=large.dll", content=dll(), headers=headers
            ).status_code
            == 413
        )
    assert not (runtime.data / "mods/BepInEx/plugins/large.dll").exists()
    assert (
        client.post("/api/v1/plugins?name=../test.dll", content=dll(), headers=headers).status_code
        == 422
    )
    client.cookies.clear()
    assert client.get("/api/v1/plugins").status_code == 401
    assert client.post(url, content=dll(), headers=headers).status_code == 401
