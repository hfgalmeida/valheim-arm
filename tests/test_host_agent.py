import importlib.util
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
from unittest.mock import Mock, patch

import pytest

spec = importlib.util.spec_from_file_location(
    "host_agent", Path(__file__).resolve().parents[1] / "host-agent.py"
)
agent_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(agent_module)


class Lock:
    closed = False

    def close(self):
        self.closed = True


def test_agent_limits_requests_and_keeps_job_across_reconnection(tmp_path):
    release = threading.Event()
    calls = []

    def factory(root, progress):
        def update(image):
            calls.append(image)
            progress("downloading")
            release.wait(2)

        return type("Updater", (), {"update": staticmethod(update)})()

    lock = Lock()
    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with patch.object(agent_module, "update_lock", return_value=lock):
        first = agent.request("a" * 32)
        assert first["state"] == "running"
        assert agent.request("a" * 32)["request_id"] == "a" * 32
        with pytest.raises(ValueError, match="in progress"):
            agent.request("b" * 32)
        release.set()
        agent.worker.join(3)
    assert lock.closed and calls == ["hfgalmeida/valheim-arm:latest"]
    restored = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    assert restored.status()["state"] == "succeeded"
    assert restored.request("a" * 32)["state"] == "succeeded"
    with pytest.raises(ValueError, match="minute"):
        restored.request("b" * 32)


def test_running_state_is_not_replayed_after_agent_restart(tmp_path):
    path = tmp_path / "status.json"
    path.write_text(
        json.dumps({"state": "running", "request_id": "a" * 32, "internal_secret": "secret"})
    )
    factory = Mock()
    agent = agent_module.Agent(tmp_path, path, factory)
    assert agent.status()["state"] == "interrupted"
    assert "secret" not in str(agent.status())
    assert "secret" not in str(agent.request("a" * 32))
    factory.assert_not_called()


def test_agent_does_not_expose_private_errors(tmp_path):
    factory = Mock(side_effect=RuntimeError("private-host-secret"))
    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with patch.object(agent_module, "update_lock", return_value=Lock()):
        agent.request("a" * 32)
        agent.worker.join(3)
    assert agent.status()["state"] == "failed"
    assert "private-host-secret" not in json.dumps(agent.status())


def test_state_write_failure_never_starts_updater_and_releases_lock(tmp_path):
    factory = Mock()
    lock = Lock()
    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with (
        patch.object(agent_module, "update_lock", return_value=lock),
        patch.object(agent_module, "atomic_json", side_effect=OSError("disk full")),
    ):
        with pytest.raises(OSError):
            agent.request("a" * 32)
    assert lock.closed
    factory.assert_not_called()


def test_progress_write_failure_does_not_abort_rollback(tmp_path):
    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", Mock())
    with patch.object(agent_module, "atomic_json", side_effect=OSError("disk full")):
        agent.progress("rollback")
    assert agent.status()["phase"] == "rollback"


@pytest.mark.parametrize("uid,allowed", [(0, True), (10001, True), (1000, False)])
def test_socket_checks_peer_uid(uid, allowed):
    connection = Mock()
    connection.getsockopt.return_value = struct.pack("3i", 123, uid, 10001)
    with patch.object(socket, "SO_PEERCRED", 17, create=True):
        assert agent_module.peer_allowed(connection) is allowed


def test_compose_extension_preserves_custom_file_selection(tmp_path):
    (tmp_path / ".env").write_text(
        'SERVER_PASSWORD="keep"\nCOMPOSE_FILE="compose.yaml:custom.yaml"\n'
    )
    (tmp_path / "compose.yaml").write_text("services: {}")
    (tmp_path / "custom.yaml").write_text("services: {}")
    text, files = agent_module.compose_sources(tmp_path)
    assert 'SERVER_PASSWORD="keep"' in text
    assert files == ["compose.yaml", "custom.yaml"]


@pytest.mark.parametrize(
    "content", ["include: other.yaml", "services:\n  valheim:\n    extends: something"]
)
def test_compose_imports_that_escape_controlled_sources_are_rejected(tmp_path, content):
    (tmp_path / ".env").write_text("SERVER_PASSWORD=keep")
    (tmp_path / "compose.yaml").write_text(content)
    with pytest.raises(ValueError, match="include/extends"):
        agent_module.compose_sources(tmp_path)


@pytest.mark.skipif(os.name != "posix", reason="Unix peer credentials and flock on Linux")
def test_real_unix_api_accepts_only_fixed_update_and_uses_shared_lock(tmp_path):
    import http.client

    lock = agent_module.update_lock(tmp_path)
    with pytest.raises(BlockingIOError):
        agent_module.update_lock(tmp_path)
    lock.close()
    called = []

    def factory(root, progress):
        return type("Updater", (), {"update": staticmethod(lambda image: called.append(image))})()

    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with tempfile.TemporaryDirectory(prefix="vhu-") as directory:
        path = str(Path(directory) / "agent.sock")

        class Connection(http.client.HTTPConnection):
            def connect(self):
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.connect(path)

        with (
            agent_module.Server(path, agent_module.Handler) as server,
            patch.object(agent_module, "peer_allowed", return_value=True),
        ):
            server.agent = agent
            thread = threading.Thread(
                target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
            )
            thread.start()

            def request(method, url, body=None):
                connection = Connection("localhost", timeout=2)
                try:
                    connection.request(
                        method,
                        url,
                        body=json.dumps(body) if body is not None else None,
                        headers={"Content-Type": "application/json"},
                    )
                    response = connection.getresponse()
                    return response.status, json.loads(response.read())
                finally:
                    connection.close()

            try:
                assert request("GET", "/status")[0] == 200
                assert request("GET", "/docker")[0] == 404
                assert (
                    request("POST", "/update", {"request_id": "a" * 32, "image": "evil:image"})[0]
                    == 409
                )
                assert request("POST", "/update", {"request_id": "a" * 32})[0] == 202
                agent.worker.join(3)
                assert called == ["hfgalmeida/valheim-arm:latest"]
            finally:
                server.shutdown()
                thread.join(2)


@pytest.mark.parametrize("recent", [True, False])
def test_rollback_outcome_refers_only_to_current_operation(tmp_path, recent):
    result = tmp_path / ".image-update.json"
    if not recent:
        result.write_text(json.dumps({"phase": "rolled-back"}))
        os.utime(result, (1, 1))

    def factory(root, progress):
        def update(image):
            if recent:
                result.write_text(json.dumps({"phase": "rolled-back"}))
            raise RuntimeError("failed")

        return type("Updater", (), {"update": staticmethod(update)})()

    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with patch.object(agent_module, "update_lock", return_value=Lock()):
        agent.request("a" * 32)
        agent.worker.join(3)
    assert agent.status()["state"] == ("rolled_back" if recent else "failed")


def test_install_writes_service_and_permissions_without_touching_host(tmp_path):
    root = tmp_path / "installation"
    root.mkdir()
    (root / ".env").write_text('SERVER_PASSWORD="keep"\n')
    (root / "compose.yaml").write_text("services: {}\n")
    managed = tmp_path / "managed"
    (managed / "etc/systemd/system").mkdir(parents=True)
    real_path = Path
    real_stat = Path.stat
    parents = {root, *root.parents}

    def paths(value, *parts):
        text = str(value)
        if text.startswith(("/usr/local/lib/", "/etc/")):
            return real_path(managed, text.lstrip("/"), *parts)
        return real_path(value, *parts)

    def stat(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        if path in parents:
            values = list(result)
            values[0] = (values[0] & ~0o777) | 0o755
            values[4] = values[5] = 0
            return os.stat_result(values)
        return result

    with (
        patch.object(agent_module, "Path", side_effect=paths),
        patch.object(Path, "stat", stat),
        patch.object(agent_module.os, "geteuid", return_value=0, create=True),
        patch.object(agent_module.os, "chown", create=True) as chown,
        patch.object(agent_module, "update_lock", return_value=Lock()),
        patch.object(agent_module.subprocess, "run") as systemctl,
    ):
        agent_module.install(root)
    assert chown.call_count >= 4
    assert (root / "update-server.py").read_bytes() == (
        Path(__file__).resolve().parents[1] / "update-server.py"
    ).read_bytes()
    assert 'SERVER_PASSWORD="keep"' in (root / ".env").read_text()
    assert 'COMPOSE_FILE="compose.yaml:compose.update-agent.yaml"' in (root / ".env").read_text()
    unit = next((managed / "etc/systemd/system").glob("*.service"))
    assert "WantedBy=multi-user.target" in unit.read_text()
    assert [call.args[0][1] for call in systemctl.call_args_list] == [
        "daemon-reload",
        "enable",
        "restart",
    ]
    assert (root / "compose.update-agent.yaml").read_text().endswith("/run/valheim-update:ro\n")


@pytest.mark.parametrize(
    "code,expected",
    [
        ("recovery_required", "previous update is incomplete"),
        ("rollback_unverified", "previous image was reapplied"),
    ],
)
def test_update_errors_have_safe_actionable_message(tmp_path, code, expected):
    def factory(root, progress):
        def update(image):
            error = RuntimeError("private credentials must not appear")
            error.code = code
            raise error

        return type("Updater", (), {"update": staticmethod(update)})()

    agent = agent_module.Agent(tmp_path, tmp_path / "status.json", factory)
    with patch.object(agent_module, "update_lock", return_value=Lock()):
        agent.request("c" * 32)
        agent.worker.join(3)
    assert agent.status()["state"] == "failed"
    assert expected in agent.status()["message"]
    assert "private" not in agent.status()["message"]
