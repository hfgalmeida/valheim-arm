import importlib.util
import json
import sys
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest

from app.audit import Audit
from app.compatibility import operation_record
from app.runtime import Runtime

spec = importlib.util.spec_from_file_location(
    "support_collector", Path(__file__).resolve().parents[1] / "scripts/collect-diagnostics.py"
)
collector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(collector)


def test_legacy_operation_fields_preserve_user_values():
    original = {
        "state": "adiado",
        "outcome": "negado",
        "changes": {"antes": {"nome": "mundo"}, "depois": {"nome": "falha"}},
    }
    result = operation_record(original)
    assert result["state"] == "deferred" and result["outcome"] == "denied"
    assert result["changes"] == {"before": {"name": "mundo"}, "after": {"name": "falha"}}
    assert original["state"] == "adiado"


def test_legacy_queue_and_history_are_not_replayed_incorrectly(runtime):
    now = __import__("time").time()
    runtime.pending_path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"id": str(i), "action": "restart", "state": state, "created": now, "scheduled": True}
        for i, state in enumerate(("adiado", "executando", "conclu\u00eddo"))
    ]
    runtime.pending_path.write_text(json.dumps(records))
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.qsize() == 1
    assert {j["id"]: j["state"] for j in restored.jobs} == {
        "0": "queued",
        "1": "failed",
        "2": "completed",
    }


def test_legacy_audit_outcome_is_read_in_english(tmp_path):
    path = tmp_path / "config/audit.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            [{"time": 1, "outcome": "negado", "actor": {"id": "system", "username": "sistema"}}]
        )
    )
    audit = Audit(tmp_path)
    assert not audit.error
    assert audit.records[0]["outcome"] == "denied"
    assert audit.records[0]["actor"]["username"] == "system"


def test_support_redaction_keeps_timestamps_but_removes_credentials():
    text = "2026-11-17T12:34:56 version 1.0.0 192.0.2.1 2001:db8::1 password=secret Authorization: Basic c2VjcmV0 Authorization: Bearer abcdef Steam_76561198000000001 join code 123456 https://example.com/token known-password"
    redacted = collector.redact(text, ["known-password"])
    for private in (
        "192.0.2.1",
        "2001:db8::1",
        "secret",
        "c2VjcmV0",
        "abcdef",
        "76561198000000001",
        "123456",
        "example.com",
        "known-password",
    ):
        assert private not in redacted
    assert "12:34:56" in redacted and "1.0.0" in redacted


def test_container_metadata_excludes_private_fields():
    info = [
        {
            "Config": {
                "Image": "public/server:1.0.0",
                "Env": ["SERVER_PASSWORD=secret", "FEX_GAME_MODE=auto"],
                "Cmd": ["secret"],
            },
            "State": {
                "Status": "running",
                "Health": {"Status": "healthy", "Log": [{"Output": "secret"}]},
            },
            "HostConfig": {},
            "Mounts": [{"Source": "/private/location", "Destination": "/data", "Type": "bind"}],
        }
    ]
    with patch.object(collector, "json_command", return_value=info):
        report = collector.container_report("proxy", "a" * 64, False)
    encoded = json.dumps(report)
    assert "secret" not in encoded and "/private/location" not in encoded
    assert report["fex"] == {"FEX_GAME_MODE": "auto"}
    assert "recent_logs" not in report


def test_support_archive_contains_only_report_and_does_not_overwrite(tmp_path):
    output = tmp_path / "report.tar.gz"
    collector.write_archive({"version": "1.0.0"}, output)
    with tarfile.open(output) as archive:
        assert archive.getnames() == ["report.json", "README.txt"]
        assert json.load(archive.extractfile("report.json")) == {"version": "1.0.0"}
    with pytest.raises(FileExistsError):
        collector.write_archive({}, output)


def test_support_runner_bounds_output_and_timeout():
    report = collector.run([sys.executable, "-c", "print('x' * 1200000)"], timeout=10)
    assert report["ok"] and report["truncated"]
    assert len(report["output"]) <= collector.MAX_OUTPUT
    report = collector.run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.1)
    assert report == {"ok": False, "error": "Command timed out"}


def test_report_still_works_when_docker_and_systemd_are_missing(tmp_path):
    (tmp_path / ".env").write_text('SERVER_PASSWORD="sensitive"')
    with patch.object(collector, "run", return_value={"ok": False, "error": "Command unavailable"}):
        report = collector.collect(tmp_path)
    assert report["host"]["architecture"]
    assert all(not c["available"] for c in report["containers"])
    assert "sensitive" not in json.dumps(report)
