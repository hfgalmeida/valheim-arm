import io
import base64
import json
import runpy
import urllib.error
from pathlib import Path
from unittest.mock import Mock

import pytest

check = runpy.run_path(str(Path(__file__).resolve().parents[1] / "docker/check_image_version.py"))[
    "ensure_unpublished"
]


def token():
    return io.BytesIO(b'{"token":"read-only-token"}')


def http_error(status, errors=None):
    body = {"errors": errors} if errors is not None else {"message": "unavailable"}
    return urllib.error.HTTPError(
        "https://registry-1.docker.io/", status, "error", {}, io.BytesIO(json.dumps(body).encode())
    )


def test_existing_image_is_blocked():
    opener = Mock(side_effect=[token(), io.BytesIO(b"manifest")])
    with pytest.raises(RuntimeError, match="já está publicada.*VERSION"):
        check("hfgalmeida/valheim-arm", "0.1.0", opener)
    manifest = opener.call_args_list[1].args[0]
    assert manifest.full_url.endswith("/hfgalmeida/valheim-arm/manifests/0.1.0")
    assert "application/vnd.oci.image.index.v1+json" in manifest.headers["Accept"]
    assert "application/vnd.docker.distribution.manifest.v2+json" in manifest.headers["Accept"]


def test_explicitly_missing_manifest_allows_build():
    opener = Mock(side_effect=[token(), http_error(404, [{"code": "MANIFEST_UNKNOWN"}])])
    check("hfgalmeida/valheim-arm", "0.2.0", opener)
    assert opener.call_count == 2


def test_authenticated_check_uses_pat_only_at_token_endpoint():
    opener = Mock(side_effect=[token(), http_error(404, [{"code": "MANIFEST_UNKNOWN"}])])
    check("hfgalmeida/valheim-arm", "0.2.0", opener, username="owner", password="test-pat")
    auth = opener.call_args_list[0].args[0]
    manifest = opener.call_args_list[1].args[0]
    assert auth.full_url.startswith("https://auth.docker.io/token?")
    assert "pull%2Cpush" in auth.full_url
    assert "account=owner" in auth.full_url
    expected = base64.b64encode(b"owner:test-pat").decode()
    assert auth.headers["Authorization"] == "Basic " + expected
    assert manifest.headers["Authorization"] == "Bearer read-only-token"
    assert "test-pat" not in auth.full_url and "test-pat" not in manifest.full_url


def test_authenticated_existing_tag_remains_blocked():
    opener = Mock(side_effect=[token(), io.BytesIO(b"manifest")])
    with pytest.raises(RuntimeError, match="já está publicada"):
        check("hfgalmeida/valheim-arm", "0.1.0", opener, username="owner", password="test-pat")


@pytest.mark.parametrize("status", [401, 403])
def test_wrong_credentials_have_actionable_error_without_secret(status):
    opener = Mock(side_effect=[http_error(status)])
    with pytest.raises(RuntimeError) as result:
        check("hfgalmeida/valheim-arm", "0.2.0", opener, username="owner", password="sensitive-pat")
    message = str(result.value)
    assert "DOCKERHUB_TOKEN" in message and "DOCKERHUB_IMAGE" in message
    assert "sensitive-pat" not in message
    assert "publicação bloqueada" in message


@pytest.mark.parametrize("username,password", [("owner", None), (None, "test-pat")])
def test_partial_credentials_are_not_silently_downgraded_to_anonymous(username, password):
    opener = Mock()
    with pytest.raises(ValueError, match="juntos"):
        check("hfgalmeida/valheim-arm", "0.2.0", opener, username=username, password=password)
    opener.assert_not_called()


def test_access_token_response_is_supported():
    opener = Mock(
        side_effect=[
            io.BytesIO(b'{"access_token":"access-token"}'),
            http_error(404, [{"code": "MANIFEST_UNKNOWN"}]),
        ]
    )
    check("hfgalmeida/valheim-arm", "0.2.0", opener)
    assert opener.call_args_list[1].args[0].headers["Authorization"] == "Bearer access-token"


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_registry_failure_never_counts_as_available_version(status):
    opener = Mock(side_effect=[token(), http_error(status)])
    with pytest.raises(RuntimeError, match="publicação bloqueada"):
        check("hfgalmeida/valheim-arm", "0.2.0", opener)


def test_wrong_repository_is_not_treated_as_missing_tag():
    opener = Mock(side_effect=[token(), http_error(404, [{"code": "NAME_UNKNOWN"}])])
    with pytest.raises(RuntimeError, match="bloqueada"):
        check("hfgalmeida/wrong-image", "0.2.0", opener)


@pytest.mark.parametrize(
    "failure",
    [
        urllib.error.URLError("offline"),
        TimeoutError("timeout"),
        http_error(401),
        io.BytesIO(b"not-json"),
        io.BytesIO(b'{"token":""}'),
    ],
)
def test_token_failures_block_publication(failure):
    opener = Mock(side_effect=[failure])
    with pytest.raises(RuntimeError, match="bloqueada"):
        check("hfgalmeida/valheim-arm", "0.2.0", opener)
    assert opener.call_count == 1


def test_manifest_network_failure_blocks_publication():
    opener = Mock(side_effect=[token(), urllib.error.URLError("offline")])
    with pytest.raises(RuntimeError, match="bloqueada"):
        check("hfgalmeida/valheim-arm", "0.2.0", opener)


@pytest.mark.parametrize(
    "image,version",
    [
        ("hfgalmeida/valheim-arm", "v0.2.0"),
        ("hfgalmeida/valheim-arm", "0.2.0/other"),
        ("https://example.com/image", "0.2.0"),
    ],
)
def test_invalid_inputs_never_contact_registry(image, version):
    opener = Mock()
    with pytest.raises(ValueError):
        check(image, version, opener)
    opener.assert_not_called()
