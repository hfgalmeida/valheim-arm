from unittest.mock import patch, MagicMock

from app.image_version import ImageVersion, published_version


def test_only_published_arm_version_matching_latest_is_reported():
    def tag(name, digest, arch="arm64"):
        return {"name": name, "images": [{"architecture": arch, "os": "linux", "digest": digest}]}

    latest = tag("latest", "sha256:new")
    values = {
        "results": [
            tag("0.14.0", "sha256:new"),
            tag("0.15.0", "sha256:other"),
            tag("0.16.0", "sha256:new", "amd64"),
            tag("sha-abc", "sha256:new"),
        ]
    }
    response = MagicMock()
    response.__enter__.return_value = response
    with (
        patch("app.image_version.urllib.request.urlopen", return_value=response),
        patch("app.image_version.json.load", side_effect=[latest, values]),
    ):
        assert published_version() == "0.14.0"


def test_image_version_distinguishes_newer_from_rollback():
    version = ImageVersion()
    version.installed, version.available = "0.14.0", "0.15.0"
    assert version.describe()["update_available"] is True
    version.available = "0.13.0"
    assert version.describe()["update_available"] is False
    version.available = None
    assert version.describe()["update_available"] is None


def test_version_check_is_fresh_rate_limited_and_preserves_last_known_on_failure():
    version = ImageVersion()
    version.installed = "0.22.0"
    with patch("app.image_version.published_version", return_value="0.23.0") as check:
        first = version.check()
        assert first["available"] == "0.23.0" and first["update_available"]
        assert not first["checking"] and not first["stale"]
        version.check()
        check.assert_called_once()
    version.last_attempt -= 61
    with patch("app.image_version.published_version", side_effect=OSError("offline")):
        value = version.check()
    assert value["available"] == "0.23.0" and value["stale"]
    assert value["error"] and value["attempted"]


def test_version_lookup_does_not_block_while_another_lookup_is_running():
    version = ImageVersion()
    version.check_lock.acquire()
    try:
        with patch("app.image_version.published_version") as fetch:
            assert version.check()["checking"]
            fetch.assert_not_called()
    finally:
        version.check_lock.release()
