"""Installer tests use command stubs: never install Docker or touch the host."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") or ("C:/Program Files/Git/bin/bash.exe" if os.name == "nt" else None)
pytestmark = pytest.mark.skipif(not BASH, reason="Bash necessário para testar o instalador")


def shell(code, *args, env=None, input=None):
    # Feed Unix line endings even when the test runner itself is Windows.
    result = subprocess.run([BASH, "-c", 'source "$1"; shift; ' + code, "test", str(ROOT / "install.sh"), *args],
                            input=input.encode("utf-8") if input is not None else None,
                            capture_output=True, env=os.environ | (env or {}))
    result.stdout = result.stdout.decode("utf-8", errors="replace")
    result.stderr = result.stderr.decode("utf-8", errors="replace")
    return result


@pytest.mark.parametrize("ip,valid", [("203.0.113.10", True), ("192.168.1.1", True), ("256.2.3.4", False),
    ("1.2.3", False), ("127.0.0.1", False), ("0.0.0.0", False), ("$(whoami)", False)])
def test_ip_validation(ip, valid):
    assert (shell('valid_ipv4 "$1"', ip).returncode == 0) == valid


def test_config_escaping_does_not_execute_shell(tmp_path):
    password = "secret $USER # spaces 'quotes' \\ tail $(touch NEVER)"
    result = shell('env_value SERVER_PASSWORD "$TEST_PASSWORD"', env={"TEST_PASSWORD": password})
    assert result.returncode == 0, result.stderr
    escaped = password.replace("\\", "\\\\").replace('"', '\\"').replace("$", "$$")
    assert result.stdout == 'SERVER_PASSWORD="' + escaped + '"\n'
    assert "NEVER" in result.stdout


@pytest.mark.parametrize("password", ["a $USER ${NOT_DEFINED} # 'quote' \\ slash $(whoami)",
    "trailing-backslash\\", "slash\\'quote\"end", "$$literal \\n text", "senha çãõ"])
def test_compose_reads_quoted_password_literally(tmp_path, password):
    # Available in CI; a standalone Compose executable can also be supplied locally.
    standalone = os.environ.get("COMPOSE_EXE")
    command = [standalone] if standalone else [shutil.which("docker") or "docker", "compose"]
    try:
        result = subprocess.run(command + ["version"], capture_output=True, text=True, encoding="utf-8")
    except OSError:
        pytest.skip("Compose não instalado")
    if result.returncode:
        pytest.skip("Compose não instalado")
    config = shell('env_value PASSWORD "$TEST_PASSWORD"', env={"TEST_PASSWORD": password})
    (tmp_path / ".env").write_text(config.stdout, encoding="utf-8")
    compose = tmp_path / "compose.yaml"
    compose.write_text('name: installer-test\nservices:\n  example:\n    image: alpine:3.22\n    environment:\n      PASSWORD: ${PASSWORD}\n', encoding="utf-8")
    result = subprocess.run(command + ["--project-directory", str(tmp_path), "--env-file", str(tmp_path / ".env"),
                                  "-f", str(compose), "config", "--environment"], capture_output=True, text=True, encoding="utf-8")
    assert result.returncode == 0, result.stderr
    assert next(line.removeprefix("PASSWORD=") for line in result.stdout.splitlines() if line.startswith("PASSWORD=")) == password


def test_failed_download_leaves_no_partial_file(tmp_path):
    result = shell('cd "$1"; curl() { return 22; }; fetch_if_missing compose.yaml compose.yaml', str(tmp_path))
    assert result.returncode != 0
    assert not list(tmp_path.iterdir())


def test_installer_preserves_existing_files(tmp_path):
    path = tmp_path / "compose.yaml"
    path.write_text("custom-config", encoding="utf-8")
    result = shell('cd "$1"; curl() { return 99; }; fetch_if_missing compose.yaml compose.yaml', str(tmp_path))
    assert result.returncode == 0, result.stderr
    assert path.read_text() == "custom-config"


STUBS = r'''
check_platform() { :; }
sudo() { if [[ "$1" == -v ]]; then return 0; fi; "$@"; }
prepare_docker() { ROOT=(); DOCKER=(fake_docker); }
install() { mkdir -p "${@: -1}"; }
curl() {
    if [[ "${@: -1}" == https://api.ipify.org ]]; then printf '203.0.113.20'; return; fi
    while (( $# )); do
        if [[ "$1" == -o ]]; then shift; printf 'downloaded fixture\n' > "$1"; return; fi
        shift
    done
    return 1
}
fake_docker() {
    printf '%s\n' "$*" >> "$COMMAND_LOG"
    case "$*" in
        'compose ps --status running --quiet valheim')
            if [[ "${MOCK_RUNNING:-0}" == 1 ]]; then printf 'container-id'; fi ;;
        'compose run --rm --no-deps valheim set-password')
            mkdir -p data/config; printf '{"hash":"fixture"}\n' > data/config/admin.json ;;
    esac
    return 0
}
main
'''


def test_install_then_repeat_preserves_password_and_running_world(tmp_path):
    target = tmp_path / "server"
    log = tmp_path / "commands"
    env = {"VALHEIM_INSTALL_DIR": str(target), "COMMAND_LOG": str(log)}
    result = shell(STUBS, env=env, input="\n\nsecret-game-password\n")
    assert result.returncode == 0, result.stderr
    assert "secret-game-password" not in result.stdout
    assert "203.0.113.20:2456" in result.stdout
    original = (target / ".env").read_bytes()
    original_compose = (target / "compose.yaml").read_bytes()
    assert "compose pull" in log.read_text()
    assert "compose run --rm --no-deps valheim preflight" in log.read_text()
    assert "compose up -d --no-build" in log.read_text()
    log.write_text("")
    result = shell(STUBS, env=env | {"MOCK_RUNNING": "1"})
    assert result.returncode == 0, result.stderr
    assert (target / ".env").read_bytes() == original
    assert (target / "compose.yaml").read_bytes() == original_compose
    assert "compose pull" not in log.read_text()
    assert "set-password" not in log.read_text()
    assert "compose up" not in log.read_text()
