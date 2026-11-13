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
