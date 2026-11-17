from types import SimpleNamespace

from tests.plugin_fixture import dll
from tests.test_web import client_fixture


def fixture(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    root = runtime.data / "mods/BepInEx/plugins"
    root.mkdir(parents=True)
    (root / "example.dll").write_bytes(dll())
    record = client.get("/api/v1/plugins").json()["files"][0]
    return client, root, record


def test_delete_requires_confirmation_revision_csrf_and_stopped_game(runtime, monkeypatch):
    client, root, plugin = fixture(runtime, monkeypatch)
    body = {"name": plugin["name"], "revision": plugin["revision"], "confirm": True}
    assert client.post("/api/v1/plugins/delete", json=body | {"confirm": False}).status_code == 422
    assert (
        client.post("/api/v1/plugins/delete", json=body | {"revision": "b" * 64}).status_code == 409
    )
    assert (
        client.post(
            "/api/v1/plugins/delete", json=body, headers={"X-CSRF-Token": "invalid"}
        ).status_code
        == 403
    )
    runtime.process = SimpleNamespace(returncode=None)
    assert client.post("/api/v1/plugins/delete", json=body).status_code == 409
    runtime.process = None
    runtime.jobs.append({"id": "waiting", "action": "restart", "state": "deferred", "created": 0})
    assert client.post("/api/v1/plugins/delete", json=body).status_code == 409
    runtime.jobs.clear()
    assert (root / "example.dll").exists()
    client.cookies.clear()
    assert client.post("/api/v1/plugins/delete", json=body).status_code == 401


def test_delete_restore_is_audited_and_never_overwrites_uploaded_plugin(runtime, monkeypatch):
    client, root, plugin = fixture(runtime, monkeypatch)
    removed = client.post(
        "/api/v1/plugins/delete",
        json={"name": plugin["name"], "revision": plugin["revision"], "confirm": True},
    )
    assert removed.status_code == 200, removed.text
    assert not (root / "example.dll").exists()
    deleted = client.get("/api/v1/plugins").json()["deleted"]
    assert len(deleted) == 1
    body = {"id": deleted[0]["id"], "revision": deleted[0]["revision"], "confirm": True}
    assert (
        client.post("/api/v1/plugins/restore-deleted", json=body | {"confirm": False}).status_code
        == 422
    )
    (root / "example.dll").write_bytes(dll(2))
    assert client.post("/api/v1/plugins/restore-deleted", json=body).status_code == 409
    assert (root / "example.dll").read_bytes() == dll(2)
    (root / "example.dll").unlink()
    response = client.post("/api/v1/plugins/restore-deleted", json=body)
    assert response.status_code == 200, response.text
    assert (root / "example.dll").read_bytes() == dll()
    events = client.get("/api/v1/audit?operation=plugins").json()["events"]
    assert any(
        event.get("changes", {}).get("file", {}).get("after") == "recoverable deletion"
        for event in events
    )
    assert any(
        event.get("changes", {}).get("file", {}).get("after") == "instalado" for event in events
    )
    assert all(event["actor"]["username"] == "admin" for event in events)
