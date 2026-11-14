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
