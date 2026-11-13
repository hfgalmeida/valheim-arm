import json
import runpy
import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.runtime import check_crossplay


GUEST = runpy.run_path(str(Path(__file__).resolve().parents[1] / "docker/prepare_guest.py"))


def elf(path, machine=62):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x7fELF\x02\x01" + bytes(12) + struct.pack("<H", machine))


def test_guest_packages_are_downloaded_in_isolation_for_amd64(tmp_path):
    config = GUEST["apt_config"](tmp_path)
    text = config.read_text()
    assert 'APT::Architecture "amd64"' in text
    assert 'Dir::State::status "' + str(tmp_path / "status") + '"' in text
    assert 'Dir::Etc::parts "' + str(tmp_path / "empty") + '"' in text
    source = (tmp_path / "sources.list").read_text()
    assert "arch=amd64" in source and "signed-by=" in source
    assert "ports.ubuntu.com" not in source
    assert "noble-security" in source
    assert not (tmp_path / "status").read_bytes()


def test_native_arm_library_is_rejected(tmp_path):
    path = tmp_path / "libatomic.so.1"
    elf(path, machine=183)
    with pytest.raises(ValueError, match="arquitetura"):
        GUEST["check_elf"](path)
    elf(path)
    GUEST["check_elf"](path)


def test_native_arm_deb_is_rejected_before_extraction(tmp_path):
    package = tmp_path / "bad.deb"
    with patch("subprocess.check_output", return_value="Package: libatomic1\nVersion: 1\nArchitecture: arm64\n"):
        with pytest.raises(ValueError, match="arquitetura"):
            GUEST["package_metadata"](package)


def test_guest_build_checks_transitive_dependencies_using_x86_loader(tmp_path):
    directory = tmp_path / "usr/lib/x86_64-linux-gnu"
    elf(directory / "ld-linux-x86-64.so.2")
    for name in GUEST["LIBRARIES"]:
        elf(directory / name)
    with patch("subprocess.run") as run:
        GUEST["validate"](tmp_path)
    assert run.call_count == len(GUEST["LIBRARIES"])
    for call in run.call_args_list:
        command = call.args[0]
        assert command[:2] == ["FEXInterpreter", str(directory / "ld-linux-x86-64.so.2")]
        assert "--list" in command
        assert "LD_TRACE_LOADED_OBJECTS" not in call.kwargs["env"]


def test_guest_build_propagates_missing_transitive_dependency(tmp_path):
    import subprocess
    directory = tmp_path / "usr/lib/x86_64-linux-gnu"
    elf(directory / "ld-linux-x86-64.so.2")
    elf(directory / GUEST["LIBRARIES"][0])
    with patch("subprocess.run", side_effect=subprocess.CalledProcessError(127, "loader")):
        with pytest.raises(subprocess.CalledProcessError):
            GUEST["validate"](tmp_path)


def test_crossplay_reports_missing_guest_library_before_game_launch(tmp_path):
    party = tmp_path / "valheim_server_Data/Plugins/libparty.so"
    elf(party)
    result = SimpleNamespace(returncode=127, stderr="libatomic.so.1: cannot open shared object file")
    with patch("app.runtime.subprocess.run", return_value=result) as run:
        with pytest.raises(RuntimeError, match="libatomic.so.1"):
            check_crossplay(tmp_path)
    command = run.call_args.args[0]
    assert command[0] == "FEXInterpreter" and "--list" in command
    assert command[-1] == str(party)


def test_crossplay_missing_party_is_not_accepted(tmp_path):
    with pytest.raises(RuntimeError, match="Party"):
        check_crossplay(tmp_path)


def test_prepare_records_only_validated_package_provenance(tmp_path):
    root = tmp_path / "root"
    info = tmp_path / "info.json"
    info.write_text('{"fex_version":"test"}')
    run = GUEST["prepare"].__globals__

    def apt(command, **kwargs):
        if command[:2] == ["apt-get", "install"]:
            apt_path = Path(kwargs["env"]["APT_CONFIG"]).parent
            (apt_path / "cache/archives/libatomic.deb").write_bytes(b"package")

    with patch("subprocess.run", side_effect=apt), patch.dict(run, {
        "package_metadata": lambda _: {"name": "libatomic1", "version": "1", "architecture": "amd64", "sha256": "hash"},
        "validate": lambda _: None,
    }):
        GUEST["prepare"](root, info)
    data = json.loads(info.read_text())
    assert data["guest_packages"][0]["architecture"] == "amd64"
    assert "libatomic.so.1" in data["guest_libraries_checked"]
