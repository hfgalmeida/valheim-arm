from fastapi.testclient import TestClient

from app.auth import set_password
from app.web import create_app


def client_fixture(runtime, monkeypatch):
    monkeypatch.setenv("PANEL_ORIGIN", "https://server.test")
    set_password(runtime.data, "admin-test-password")
    app = create_app(runtime.data, runtime, lifecycle=False)
    client = TestClient(app, base_url="https://server.test")
    response = client.post("/api/v1/login", json={"password": "admin-test-password"}, headers={"Origin": "https://server.test"})
    assert response.status_code == 200
    client.headers.update({"Origin": "https://server.test", "X-CSRF-Token": response.json()["csrf"]})
    return client


def test_auth_csrf_and_password_rotation(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    assert client.get("/api/v1/state").status_code == 200
    assert next(c for c in client.cookies.jar if c.name == "session").secure
    assert client.patch("/api/v1/config", json={"changes": {"public": False}}, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert client.patch("/api/v1/config", json={"changes": {"public": False}}, headers={"Origin": "https://evil.test"}).status_code == 403
    set_password(runtime.data, "new-admin-password")
    assert client.get("/api/v1/state").status_code == 401


def test_cloud_settings_require_auth_csrf_and_never_return_tokens(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    body = {"enabled": True, "folder": "Valheim/backups", "config": '[account]\ntype=dropbox\ntoken={"access_token":"secret-access","refresh_token":"secret-refresh"}'}
    assert client.put("/api/v1/cloud", json=body, headers={"X-CSRF-Token": "wrong"}).status_code == 403
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
    assert client.patch("/api/v1/config", json={"changes": {"world_name": "New"}, "confirm_world": True}).status_code == 200
    response = client.patch("/api/v1/config", json={"changes": {"server_password": "bad"}})
    assert response.status_code == 422
    assert "input_value" not in response.text


def test_users_and_job_queue(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    assert client.put("/api/v1/users/admins", json={"entries": ["Steam_123", "Steam_123"]}).json() == {"entries": ["Steam_123"]}
    assert client.get("/api/v1/users/admins").json()["entries"] == ["Steam_123"]
    assert client.put("/api/v1/users/admins", json={"entries": ["id\nINJECT"]}).status_code == 422
    response = client.post("/api/v1/actions", json={"action": "backup"})
    assert response.status_code == 202
    job = response.json()
    assert client.get("/api/v1/jobs/" + job["id"]).json()["state"] == "fila"
    assert client.post("/api/v1/actions", json={"action": "restore", "backup": "x.tar.gz"}).status_code == 422
    assert client.get("/").status_code == 200
    assert client.get("/panel.js").status_code == 200


def test_login_origin_and_rate_limit(runtime, monkeypatch):
    monkeypatch.setenv("PANEL_ORIGIN", "https://server.test")
    app = create_app(runtime.data, runtime, lifecycle=False)
    client = TestClient(app, base_url="https://server.test")
    assert client.post("/api/v1/login", json={"password": "bad"}).status_code == 403
    for _ in range(5):
        assert client.post("/api/v1/login", json={"password": "bad"}, headers={"Origin": "https://server.test"}).status_code == 401
    response = client.post("/api/v1/login", json={"password": "bad"}, headers={"Origin": "https://server.test"})
    assert "Muitas tentativas" in response.text
    assert client.get("/api/v1/backups").status_code == 401
