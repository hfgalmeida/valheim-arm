import asyncio
import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch

from app.alerts import Alerts
from app.backups import Backups
from app.backup_freshness import observe
from tests.test_alerts import URL
from tests.test_web import client_fixture


def published(backups, timestamp):
    name = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y%m%dT%H%M%S%fZ.tar.gz")
    path = backups.root / name
    path.write_bytes(b"published-test-archive")
    path.with_suffix(path.suffix + ".sha256").write_text("a" * 64 + "\n")
    return path


def test_latest_complete_backup_ignores_mtime_partial_and_future_files(tmp_path):
    backups = Backups(tmp_path)
    now = 1800000000
    valid = published(backups, now - 3600)
    os.utime(valid, (now + 86400, now + 86400))
    pending = published(backups, now - 1)
    pending.with_suffix(pending.suffix + ".sha256").unlink()
    published(backups, now + 86400)
    (backups.root / "20990101T000000000000Z.tar.gz.partial").write_bytes(b"unfinished")
    (backups.root / "not-a-timestamp.tar.gz").write_bytes(b"untrusted-date")
    assert backups.latest_completed(now) == {"time": now - 3600, "name": valid.name}
    valid.with_suffix(valid.suffix + ".sha256").write_text("partial-checksum")
    assert backups.latest_completed(now) is None


def test_real_created_backup_has_completion_date(runtime):
    (runtime.data / "saves/world.db").write_bytes(b"world")
    name = runtime.backups.create("test", runtime.config.effective())
    latest = runtime.backups.latest_completed()
    assert latest["name"] == name
    assert (
        datetime.fromtimestamp(latest["time"], timezone.utc).strftime("%Y%m%dT%H%M%S%fZ.tar.gz")
        == name
    )


def test_missing_grace_survives_restart_then_notifies_once_and_normalizes(runtime):
    now = [1800000000]

    async def scenario():
        alerts = runtime.alerts
        alerts.configure(True, URL, 1, 0)
        with patch.object(alerts, "post") as send:
            await alerts.poll(runtime)
            assert alerts.describe()["freshness"]["local"]["state"] == "grace"
            assert not send.called
        now[0] += 3599
        assert "backup_age" not in json.loads(alerts.state_path.read_text())
        assert json.loads(alerts.age_path.read_text())["backup_age"]["monitor_since"] == 1800000000
        restored = Alerts(runtime.data)
        with patch.object(restored, "post") as send:
            await restored.poll(runtime)
            assert not send.called
            now[0] += 1
            await restored.poll(runtime)
            await restored.poll(runtime)
            assert send.call_count == 1 and "overdue" in send.call_args.args[0]
            assert "backup_age" in restored.describe()["active"]
            runtime.backups.latest_completed.return_value = {
                "time": now[0],
                "name": "Completed.tar.gz",
            }
            now[0] += 301
            await restored.poll(runtime)
            assert send.call_count == 2 and "again" in send.call_args.args[0]
            assert restored.describe()["freshness"]["local"]["state"] == "ok"
            assert "backup_age" not in restored.describe()["active"]

    with (
        patch("app.alerts.time.time", side_effect=lambda: now[0]),
        patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)),
        patch.object(runtime.backups, "latest_completed", return_value=None),
    ):
        asyncio.run(scenario())
    assert not runtime.jobs and runtime.run_intent.running


def test_cloud_disabled_or_threshold_disabled_does_not_send_false_recovery(runtime):
    now = [1800000000]
    cloud = {"enabled": True, "error": None, "monitor_since": now[0], "last_success": None}

    async def scenario():
        alerts = runtime.alerts
        alerts.configure(True, URL, 0, 1)
        with patch.object(alerts, "post") as send:
            await alerts.poll(runtime)
            now[0] += 3601
            await alerts.poll(runtime)
            assert send.call_count == 1 and "cloud_age" in alerts.describe()["active"]
            cloud["enabled"] = False
            now[0] += 301
            await alerts.poll(runtime)
            assert send.call_count == 1
            assert alerts.describe()["freshness"]["cloud"]["state"] == "inactive"
            cloud["enabled"] = True
            cloud["monitor_since"] = now[0]
            await alerts.poll(runtime)
            assert not alerts.describe()["active"]
            now[0] += 3601
            await alerts.poll(runtime)
            assert send.call_count == 2
            alerts.configure(True, backup_max_age_hours=0, cloud_max_age_hours=0)
            now[0] += 301
            await alerts.poll(runtime)
            assert send.call_count == 2

    with (
        patch("app.alerts.time.time", side_effect=lambda: now[0]),
        patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)),
        patch.object(runtime.cloud, "describe", side_effect=lambda: dict(cloud)),
    ):
        asyncio.run(scenario())


def test_overdue_visible_without_discord_and_unreadable_archive_does_not_claim_recovery(runtime):
    now = 1800000000

    async def scenario():
        alerts = runtime.alerts
        alerts.configure(False, backup_max_age_hours=1)
        with (
            patch.object(alerts, "post") as send,
            patch.object(
                runtime.backups,
                "latest_completed",
                return_value={"time": now - 7200, "name": "old.tar.gz"},
            ) as latest,
        ):
            await alerts.poll(runtime)
            assert alerts.describe()["freshness"]["local"]["state"] == "overdue"
            latest.side_effect = PermissionError("private path")
            await alerts.poll(runtime)
            assert alerts.describe()["freshness"]["local"]["state"] == "unknown"
            assert "backup_age" in alerts.describe()["active"]
            assert not send.called
            alerts.configure(False, backup_max_age_hours=0)
            await alerts.poll(runtime)
            assert "backup_age" not in alerts.describe()["active"]
            assert alerts.describe()["freshness"]["local"]["state"] == "disabled"

    with (
        patch("app.alerts.time.time", return_value=now),
        patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)),
    ):
        asyncio.run(scenario())


def test_deleted_backups_and_future_dates_cannot_look_recent():
    now = 1800000000
    status, incident = observe({}, {"time": now, "name": "world.tar.gz"}, now, 36)
    assert status["state"] == "ok"
    status, incident = observe(incident, None, now + 1, 36)
    assert status["state"] == "overdue" and incident["active"]
    status, _ = observe({}, {"time": now + 86400, "name": "future.tar.gz"}, now, 36)
    assert status["state"] == "grace" and status["last_success"] is None


def test_thresholds_are_private_audited_and_legacy_requests_preserve_them(runtime, monkeypatch):
    client = client_fixture(runtime, monkeypatch)
    body = {"enabled": False, "backup_max_age_hours": 48, "cloud_max_age_hours": 72}
    assert (
        client.put("/api/v1/alerts", json=body, headers={"X-CSRF-Token": "bad"}).status_code == 403
    )
    result = client.put("/api/v1/alerts", json=body)
    assert result.status_code == 200, result.text
    assert result.json()["freshness"]["local"]["max_age_hours"] == 48
    assert client.put("/api/v1/alerts", json={"enabled": False}).json()["cloud_max_age_hours"] == 72
    for value in (-1, 721, 1.5, True, "48"):
        assert (
            client.put("/api/v1/alerts", json=body | {"backup_max_age_hours": value}).status_code
            == 422
        )
    events = client.get("/api/v1/audit?operation=alerts").json()["events"]
    assert any(
        event.get("changes", {}).get("backup_max_age_hours", {}).get("after") == 48
        for event in events
    )
    client.cookies.clear()
    assert client.get("/api/v1/alerts").status_code == 401
