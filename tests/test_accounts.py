import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.auth import set_password
from app.runtime import Runtime
from tests.test_web import client_fixture


def login(client, username, password):
    response = client.post(
        "/api/v1/login",
        json={"username": username, "password": password},
        headers={"Origin": "https://server.test"},
    )
    assert response.status_code == 200, response.text
    client.headers.update(
        {"Origin": "https://server.test", "X-CSRF-Token": response.json()["csrf"]}
    )
    return response.json()


def operator_fixture(runtime, monkeypatch):
    admin = client_fixture(runtime, monkeypatch)
    created = admin.post(
        "/api/v1/accounts",
        json={
            "username": "operator-one",
            "role": "operator",
            "password": "temporary-operator-password",
        },
    )
    assert created.status_code == 201
    operator = TestClient(admin.app, base_url="https://server.test")
    login(operator, "operator-one", "temporary-operator-password")
    response = operator.post(
        "/api/v1/password",
        json={
            "current_password": "temporary-operator-password",
            "password": "personal-operator-password",
        },
    )
    assert response.status_code == 200, response.text
    login(operator, "operator-one", "personal-operator-password")
    return admin, operator, created.json()


def test_legacy_admin_credential_and_local_recovery_remain_valid(runtime, monkeypatch):
    admin = client_fixture(runtime, monkeypatch)
    assert admin.get("/api/v1/session").json()["user"]["username"] == "admin"
    assert not (runtime.data / "config/panel-users.json").exists()
    set_password(runtime.data, "recovery-admin-password")
    assert admin.get("/api/v1/session").status_code == 401
    login(admin, "admin", "recovery-admin-password")
    assert admin.get("/api/v1/accounts").status_code == 200


def test_first_access_allows_only_password_change_session_and_logout(runtime, monkeypatch):
    admin = client_fixture(runtime, monkeypatch)
    user = admin.post(
        "/api/v1/accounts",
        json={"username": "new-user", "role": "operator", "password": "temporary-password"},
    ).json()
    operator = TestClient(admin.app, base_url="https://server.test")
    session = login(operator, "new-user", "temporary-password")
    assert session["user"]["must_change_password"]
    for path in ("/state", "/config", "/events", "/accounts", "/audit"):
        assert operator.get("/api/v1" + path).status_code == 403
    assert operator.post("/api/v1/actions", json={"action": "start"}).status_code == 403
    assert operator.get("/api/v1/session").status_code == 200
    assert (
        operator.post(
            "/api/v1/password",
            json={"current_password": "temporary-password", "password": "temporary-password"},
        ).status_code
        == 422
    )
    assert (
        operator.post(
            "/api/v1/password", json={"current_password": "wrong", "password": "personal-password"}
        ).status_code
        == 403
    )
    assert admin.get("/api/v1/accounts").json()["users"][1]["id"] == user["id"]


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("get", "/config", None),
        ("patch", "/config", {"changes": {"public": False}}),
        ("get", "/backups", None),
        ("get", "/backups/private.tar.gz", None),
        ("get", "/cloud", None),
        ("put", "/cloud", {"enabled": False, "folder": "world"}),
        ("get", "/plugins", None),
        ("post", "/plugins", {}),
        ("post", "/plugins/delete", {}),
        ("post", "/plugins/restore-deleted", {}),
        ("get", "/mod-configs", None),
        ("get", "/mod-config?path=private.cfg", None),
        ("get", "/alerts", None),
        ("post", "/alerts/test", {}),
        ("get", "/users/admins", None),
        ("put", "/player-directory", {"id": "Xbox_12345", "name": "Player", "confirm": True}),
        ("put", "/users/admins", {"entries": []}),
        ("get", "/accounts", None),
        (
            "post",
            "/accounts",
            {"username": "hacker", "role": "admin", "password": "secret-admin-password"},
        ),
        ("get", "/audit", None),
        ("get", "/image-update", None),
        ("get", "/image-version", None),
        ("post", "/image-update", {"confirm": True, "request_id": "a" * 32}),
        ("post", "/actions", {"action": "backup"}),
        ("post", "/actions", {"action": "update"}),
        ("post", "/actions", {"action": "restore", "backup": "private.tar.gz", "confirm": True}),
    ],
)
def test_operator_cannot_access_admin_operations(runtime, monkeypatch, method, path, body):
    _, operator, _ = operator_fixture(runtime, monkeypatch)
    args = {"json": body} if body is not None else {}
    response = getattr(operator, method)("/api/v1" + path, **args)
    assert response.status_code == 403, response.text


def test_operator_state_and_job_lookup_never_leak_other_users_or_private_details(
    runtime, monkeypatch
):
    admin, operator, _ = operator_fixture(runtime, monkeypatch)
    runtime.logs.append({"time": 1, "message": "private-world-details"})
    runtime.last_failure = {"time": 1, "source": "private", "message": "secret-diagnostics"}
    other = admin.post("/api/v1/actions", json={"action": "backup"}).json()
    own = operator.post("/api/v1/actions", json={"action": "restart"}).json()
    response = operator.get("/api/v1/state")
    value = response.json()
    assert [j["id"] for j in value["jobs"]] == [own["id"]]
    assert value["logs"] == []
    assert not any(
        key in value for key in ("cloud", "alerts", "mods", "image", "image_version", "startup")
    )
    assert "private" not in response.text and "secret-diagnostics" not in response.text
    assert "actor_credential" not in response.text
    assert operator.get("/api/v1/jobs/" + other["id"]).status_code == 404
    assert operator.get("/api/v1/jobs/" + own["id"]).status_code == 200
    assert "actor_credential" not in admin.get("/api/v1/state").text


def test_operator_sse_is_filtered_and_stops_after_account_revocation(runtime, monkeypatch):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    runtime.logs.append({"time": 1, "message": "private-server-log"})
    token = operator.cookies.get("session")
    endpoint = next(
        r.endpoint for r in admin.app.routes if getattr(r, "path", "") == "/api/v1/events"
    )

    async def scenario():
        request = SimpleNamespace(
            cookies={"session": token}, is_disconnected=AsyncMock(return_value=False)
        )
        response = await endpoint(request)
        event = await response.body_iterator.__anext__()
        assert "private-server-log" not in event
        assert '"logs": []' in event
        admin.app.state.auth.accounts.change(user["id"], "operator", False)
        with patch("app.web.asyncio.sleep", new=AsyncMock()):
            with pytest.raises(StopAsyncIteration):
                await response.body_iterator.__anext__()

    asyncio.run(scenario())


def test_disable_reset_and_role_change_invalidate_sessions_and_queued_jobs(runtime, monkeypatch):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    own = operator.post("/api/v1/actions", json={"action": "restart"}).json()
    assert (
        admin.patch(
            "/api/v1/accounts/" + user["id"], json={"role": "operator", "enabled": False}
        ).status_code
        == 200
    )
    assert operator.get("/api/v1/session").status_code == 401
    assert next(j for j in runtime.jobs if j["id"] == own["id"])["state"] == "cancelled"
    admin.patch("/api/v1/accounts/" + user["id"], json={"role": "operator", "enabled": True})
    login(operator, "operator-one", "personal-operator-password")
    assert (
        admin.post(
            "/api/v1/accounts/" + user["id"] + "/password",
            json={"password": "reset-temporary-password"},
        ).status_code
        == 200
    )
    assert operator.get("/api/v1/session").status_code == 401
    assert login(operator, "operator-one", "reset-temporary-password")["user"][
        "must_change_password"
    ]
    assert (
        admin.patch(
            "/api/v1/accounts/admin", json={"role": "operator", "enabled": False}
        ).status_code
        == 409
    )


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_operator_stop_and_restart_wait_without_changing_run_intent(runtime, monkeypatch, action):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    runtime.process = SimpleNamespace(pid=42, returncode=None)
    runtime.running = runtime.config.effective()
    runtime.game_ready = True
    runtime.players.consume("Connections 2 ZDOS:1")
    job = operator.post("/api/v1/actions", json={"action": action}).json()
    pending = next(j for j in runtime.jobs if j["id"] == job["id"])
    assert pending["requires_idle"] and runtime.run_intent.running

    async def scenario():
        task = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    with patch("app.runtime.os.killpg", create=True) as kill:
        asyncio.run(scenario())
    assert pending["state"] == "deferred"
    assert runtime.run_intent.running
    kill.assert_not_called()
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.qsize() == 1
    assert restored.jobs[-1]["actor"]["id"] == user["id"]


def test_audit_persists_attribution_changes_results_and_never_credentials(runtime, monkeypatch):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    admin.patch(
        "/api/v1/config",
        json={"changes": {"public": False, "server_password": "changed-game-secret"}},
    )
    job = operator.post("/api/v1/actions", json={"action": "start"}).json()

    async def execute(item):
        item["result"] = "internal-result-not-for-audit"

    runtime.execute = execute

    async def scenario():
        task = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(scenario())
    result = admin.get("/api/v1/audit?user=operator-one&operation=start").json()["events"]
    assert {r["outcome"] for r in result} >= {"queued", "running", "completed"}
    assert all(r["job_id"] == job["id"] and r["actor"]["id"] == user["id"] for r in result)
    raw = runtime.audit.path.read_text()
    for secret in (
        "temporary-operator-password",
        "personal-operator-password",
        "changed-game-secret",
        "internal-result-not-for-audit",
        "$argon2",
    ):
        assert secret not in raw
    events = admin.get("/api/v1/audit?operation=configuration").json()["events"]
    event = next(r for r in events if "changes" in r)
    assert event["changes"]["public"] == {"before": True, "after": False}
    assert event["changes"]["server_password"] == {"changed": True}
    from app.audit import Audit

    restored = Audit(runtime.data)
    assert restored.query(user="operator-one", operation="start")


def test_audit_write_failure_refuses_mutation_before_side_effects(runtime, monkeypatch):
    admin = client_fixture(runtime, monkeypatch)
    with patch("app.audit.atomic_json", side_effect=OSError("disk full")):
        response = admin.post("/api/v1/actions", json={"action": "stop"})
    assert response.status_code == 503
    assert runtime.run_intent.running
    assert runtime.queue.empty()


def test_password_change_is_limited_and_rejects_stale_credentials(runtime, monkeypatch):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    for _ in range(5):
        assert (
            operator.post(
                "/api/v1/password",
                json={"current_password": "wrong", "password": "new-personal-password"},
            ).status_code
            == 403
        )
    assert (
        operator.post(
            "/api/v1/password",
            json={"current_password": "wrong", "password": "new-personal-password"},
        ).status_code
        == 429
    )
    account = admin.app.state.auth.accounts
    old = account.fingerprint(account.get(user["id"]))
    account.password(user["id"], "administrator-reset-password", True)
    with pytest.raises(ValueError, match="Credenciais"):
        account.password(user["id"], "must-not-override-reset", expected=old)


@pytest.mark.parametrize("fail_persistence", [False, True])
def test_operator_stop_records_intent_only_after_idle_and_before_signal(
    runtime, monkeypatch, fail_persistence
):
    import signal

    admin, operator, _ = operator_fixture(runtime, monkeypatch)

    async def scenario():
        runtime.process = SimpleNamespace(pid=42, returncode=None)
        runtime.running = runtime.config.effective().model_copy(update={"crossplay": True})
        runtime.game_ready = True
        runtime.players.consume("Connections 0 ZDOS:1")
        runtime.players.empty_since -= 121
        saved = asyncio.Event()

        async def reader():
            await saved.wait()
            runtime.last_save = __import__("time").monotonic()
            runtime.process.returncode = 0

        runtime.reader = asyncio.create_task(reader())
        item = operator.post("/api/v1/actions", json={"action": "stop"}).json()
        job = next(j for j in runtime.jobs if j["id"] == item["id"])

        def signal_game(pid, sig):
            if sig == signal.SIGINT:
                assert not runtime.run_intent.running
                saved.set()

        with (
            patch("app.runtime.signal.SIGSTOP", 19, create=True),
            patch("app.runtime.signal.SIGCONT", 18, create=True),
            patch("app.runtime.os.killpg", side_effect=signal_game, create=True) as kill,
        ):
            if fail_persistence:
                with patch.object(runtime.run_intent, "set", side_effect=OSError("disk full")):
                    with pytest.raises(OSError):
                        await runtime.execute(job)
                assert all(call.args[1] != signal.SIGINT for call in kill.call_args_list)
                assert runtime.run_intent.running
                runtime.reader.cancel()
                await asyncio.gather(runtime.reader, return_exceptions=True)
            else:
                await runtime.execute(job)
                assert not runtime.run_intent.running
                assert runtime.clean_stop

    asyncio.run(scenario())


def test_interrupted_operation_records_audit_failure_on_next_boot(runtime, monkeypatch):
    admin, operator, _ = operator_fixture(runtime, monkeypatch)
    response = operator.post("/api/v1/actions", json={"action": "restart"}).json()
    job = next(j for j in runtime.jobs if j["id"] == response["id"])
    job["state"] = "running"
    runtime.persist_pending()
    restored = Runtime(runtime.data, runtime.config)
    event = next(r for r in restored.audit.query(operation="restart") if r["outcome"] == "failed")
    assert event["actor"]["username"] == "operator-one"
    assert event["job_id"] == job["id"]
    assert restored.queue.empty()


def test_audit_retention_filters_and_corruption_fail_closed(tmp_path):
    import time
    from app.audit import Audit, LIMIT

    audit = Audit(tmp_path)
    now = time.time()
    audit.records = [
        {
            "id": str(i),
            "time": now,
            "actor": {"username": "user"},
            "operation": "start",
            "outcome": "queued",
        }
        for i in range(LIMIT + 3)
    ]
    audit.records.insert(0, {"time": 1})
    with patch("app.audit.atomic_json"):
        audit.append({"id": "admin", "username": "admin", "role": "admin"}, "stop", "completed")
    assert len(audit.records) == LIMIT
    assert audit.query(user="admin", operation="stop")[0]["outcome"] == "completed"
    assert not audit.query(since=now + 100)
    audit.path.parent.mkdir(parents=True, exist_ok=True)
    audit.path.write_text("corrupt")
    broken = Audit(tmp_path)
    with pytest.raises(OSError):
        broken.append(None, "start", "queued")
    assert audit.path.read_text() == "corrupt"


def test_image_audit_distinguishes_request_failure_from_final_job_outcome(tmp_path):
    from app.audit import Audit

    audit = Audit(tmp_path)
    actor = {"id": "admin", "username": "admin", "role": "admin"}
    audit.append(actor, "image-update", "failed", request_id="request", job_id="job")
    audit.image_result({"state": "failed", "request_id": "job"})
    audit.image_result({"state": "failed", "request_id": "job"})
    assert len([r for r in audit.records if r.get("kind") == "job"]) == 1
    assert audit.records[-1]["actor"] == actor


def test_revoked_operator_start_does_not_change_stopped_intent(runtime, monkeypatch):
    admin, operator, user = operator_fixture(runtime, monkeypatch)
    runtime.set_run_intent(False)
    response = operator.post("/api/v1/actions", json={"action": "start"})
    assert response.status_code == 202
    assert not runtime.run_intent.running
    admin.patch("/api/v1/accounts/" + user["id"], json={"role": "operator", "enabled": False})
    assert not runtime.run_intent.running
    assert runtime.jobs[-1]["state"] == "cancelled"


def test_operator_cannot_forge_attribution_or_disable_idle_guard(runtime, monkeypatch):
    _, operator, _ = operator_fixture(runtime, monkeypatch)
    for body in (
        {"action": "stop", "requires_idle": False},
        {"action": "restart", "actor": {"id": "admin", "role": "admin"}},
    ):
        assert operator.post("/api/v1/actions", json=body).status_code == 422
    assert runtime.queue.empty()
