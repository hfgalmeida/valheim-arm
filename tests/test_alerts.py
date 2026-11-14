import asyncio
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.alerts import Alerts, validate_url

URL = "https://discord.com/api/webhooks/123/" + "a" * 40


@pytest.mark.parametrize(
    "url",
    [
        "http://discord.com/api/webhooks/1/abc",
        "https://localhost/",
        URL + "?x=y",
        URL + "/../",
        URL.replace("discord.com", "discord.com.evil.test"),
        URL.replace("https://", "https://user@"),
    ],
)
def test_reject_untrusted_webhooks(url):
    with pytest.raises(ValueError):
        validate_url(url)


def test_settings_are_private_and_persist_without_echo(tmp_path):
    alerts = Alerts(tmp_path)
    with pytest.raises(ValueError):
        alerts.configure(True)
    alerts.configure(True, URL)
    assert URL not in json.dumps(alerts.describe())
    alerts.configure(False)
    restored = Alerts(tmp_path)
    assert restored.settings["url"] == URL
    assert not restored.describe()["enabled"]


def test_incident_dedup_recovery_and_restart(runtime):
    async def scenario():
        alerts = runtime.alerts
        alerts.configure(True, URL)
        alerts.condition("backup", True)
        with patch.object(alerts, "post") as send:
            await alerts.poll(runtime)
            await alerts.poll(runtime)
            assert send.call_count == 1
            restored = Alerts(runtime.data)
            with patch.object(restored, "post") as resend:
                await restored.poll(runtime)
                assert resend.call_count == 0
            alerts.condition("backup", False)
            await alerts.poll(runtime)
            assert send.call_count == 1
            alerts.incidents["backup"]["next_send"] = 0
            await alerts.poll(runtime)
            assert send.call_count == 2
            assert "normalizado" in send.call_args.args[0]

    with patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)):
        asyncio.run(scenario())


def test_delivery_failure_is_throttled_and_retried(runtime):
    async def scenario():
        alerts = runtime.alerts
        alerts.configure(True, URL)
        alerts.condition("backup", True)
        with patch.object(alerts, "post", side_effect=ValueError("Entrega falhou")) as send:
            await alerts.poll(runtime)
            await alerts.poll(runtime)
            assert send.call_count == 1
            assert alerts.error
            alerts.incidents["backup"]["next_send"] = 0
            await alerts.poll(runtime)
            assert send.call_count == 2

    with patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=20 * 1024**3)):
        asyncio.run(scenario())


def test_disk_hysteresis_cloud_and_game_recovery(runtime):
    async def scenario():
        alerts = runtime.alerts
        with patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=5 * 1024**3)):
            runtime.recovery_error = "suspended"
            with patch.object(
                runtime.cloud, "describe", return_value={"enabled": True, "error": "failed"}
            ):
                await alerts.poll(runtime)
        assert set(alerts.describe()["active"]) == {"disk", "game", "cloud"}
        with patch(
            "app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=int(6.5 * 1024**3))
        ):
            await alerts.poll(runtime)
        assert "disk" in alerts.describe()["active"]
        runtime.recovery_error = None
        runtime.process = SimpleNamespace(returncode=None)
        runtime.game_ready = True
        with patch("app.alerts.shutil.disk_usage", return_value=SimpleNamespace(free=8 * 1024**3)):
            with patch.object(
                runtime.cloud,
                "describe",
                return_value={"enabled": True, "error": None, "last_success": 10**12},
            ):
                await alerts.poll(runtime)
        assert not alerts.describe()["active"]

    asyncio.run(scenario())


def test_test_notification_cooldown(runtime):
    async def scenario():
        alerts = runtime.alerts
        alerts.configure(False, URL)
        with patch.object(alerts, "post") as send:
            assert await alerts.test() == {"sent": True}
            with pytest.raises(ValueError):
                await alerts.test()
            assert send.call_count == 1

    asyncio.run(scenario())


def test_discord_payload_blocks_mentions_and_confirms_delivery(tmp_path):
    alerts = Alerts(tmp_path)
    alerts.configure(True, URL)
    with patch("app.alerts.urllib.request.build_opener") as opener:
        opener.return_value.open.return_value.__enter__.return_value.status = 200
        alerts.post("Test @everyone")
        request = opener.return_value.open.call_args.args[0]
        assert request.full_url == URL + "?wait=true"
        assert json.loads(request.data)["allowed_mentions"] == {"parse": []}


def test_snapshot_failure_and_success_update_incident(runtime):
    async def scenario():
        with patch.object(runtime.backups, "create", side_effect=ValueError("disk")):
            with pytest.raises(ValueError):
                await runtime.snapshot("backup")
        assert "backup" in runtime.alerts.describe()["active"]
        with (
            patch.object(runtime.backups, "create", return_value="save.tar.gz"),
            patch.object(runtime.backups, "prune"),
        ):
            await runtime.snapshot("backup")
        assert "backup" not in runtime.alerts.describe()["active"]

    asyncio.run(scenario())
