import asyncio
from unittest.mock import patch

from tests.test_web import client_fixture


def test_image_update_requires_session_csrf_confirmation_and_rejects_arguments(
    runtime, monkeypatch
):
    client = client_fixture(runtime, monkeypatch)
    body = {"confirm": True, "request_id": "a" * 32}
    with patch.object(
        runtime.host_updates, "start", return_value={"available": True, "state": "running"}
    ) as start:
        assert (
            client.post(
                "/api/v1/image-update", json=body, headers={"X-CSRF-Token": "wrong"}
            ).status_code
            == 403
        )
        assert (
            client.post("/api/v1/image-update", json=body | {"confirm": False}).status_code == 422
        )
        assert client.post("/api/v1/image-update", json=body | {"image": "evil"}).status_code == 422
        assert client.post("/api/v1/image-update", json=body).status_code == 202
        start.assert_called_once_with("a" * 32)
        client.cookies.clear()
        assert client.post("/api/v1/image-update", json=body).status_code == 401
        assert client.get("/api/v1/image-update").status_code == 401


def test_busy_image_update_blocks_game_writes_and_scheduled_jobs(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    runtime.host_updates.current = {"available": True, "state": "running", "message": "updating"}
    with patch.object(runtime.host_updates, "status", return_value=runtime.host_updates.current):
        assert client.post("/api/v1/actions", json={"action": "stop"}).status_code == 409
        assert (
            client.patch("/api/v1/config", json={"changes": {"public": False}}).status_code == 409
        )
        assert client.get("/api/v1/state").status_code == 200
    asyncio.run(runtime.tick())
    assert runtime.queue.empty()


def test_active_game_job_blocks_image_update(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    runtime.submit("backup")
    with patch.object(runtime.host_updates, "start") as start:
        assert (
            client.post(
                "/api/v1/image-update", json={"confirm": True, "request_id": "a" * 32}
            ).status_code
            == 409
        )
        start.assert_not_called()


def test_missing_host_service_is_reported_without_private_path(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    with patch.object(
        runtime.host_updates, "start", side_effect=OSError("private path /host/private")
    ):
        response = client.post(
            "/api/v1/image-update", json={"confirm": True, "request_id": "a" * 32}
        )
    assert response.status_code == 503
    assert "/host/private" not in response.text


def test_client_preserves_unknown_inflight_update_until_status_is_confirmed():
    from app.host_updates import HostUpdates

    client = HostUpdates()
    with patch.object(client, "request", side_effect=OSError("disconnected")):
        try:
            client.start("a" * 32)
        except OSError:
            pass
        assert client.busy
        assert not client.status()["available"]
        assert client.busy
    with patch.object(
        client, "request", return_value={"available": True, "state": "succeeded", "message": "done"}
    ):
        assert client.status()["state"] == "succeeded"
    assert not client.busy
