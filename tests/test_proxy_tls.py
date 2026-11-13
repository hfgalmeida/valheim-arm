"""Real Caddy TLS test: no SNI, as with a browser connecting by IP behind NAT."""
import json
import os
import shutil
import socket
import ssl
import subprocess
import time
from pathlib import Path

import pytest


CADDY = os.environ.get("CADDY_EXE") or shutil.which("caddy")


@pytest.mark.skipif(not CADDY, reason="Caddy necessário; configure CADDY_EXE para testar o TLS real")
def test_proxy_presents_public_ip_certificate_without_sni(tmp_path):
    ip = "192.0.2.10"
    source = Path(__file__).resolve().parents[1] / "docker/Caddyfile"
    caddyfile = tmp_path / "Caddyfile"
    # Tests must never install a certificate authority in the host trust store.
    caddyfile.write_text(source.read_text().replace("admin off", "admin off\n    skip_install_trust"))
    env = os.environ | {"SERVER_IP": ip}
    result = subprocess.run([CADDY, "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
                            capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    for server in config["apps"]["http"]["servers"].values():
        server["listen"] = [f"127.0.0.1:{port}"]
    storage = tmp_path / "storage"
    config["storage"] = {"module": "file_system", "root": str(storage)}
    config_path = tmp_path / "caddy.json"
    config_path.write_text(json.dumps(config))
    with (tmp_path / "caddy.log").open("w") as logs:
        process = subprocess.Popen([CADDY, "run", "--config", str(config_path)], env=env,
            stdout=logs, stderr=logs, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            deadline = time.monotonic() + 10
            last_error = None
            while time.monotonic() < deadline and process.poll() is None:
                try:
                    ca = storage / "pki/authorities/local/root.crt"
                    context = ssl.create_default_context(cafile=str(ca))
                    context.check_hostname = False
                    with socket.create_connection(("127.0.0.1", port), timeout=1) as sock:
                        with context.wrap_socket(sock, server_hostname=None) as tls:
                            assert ("IP Address", ip) in tls.getpeercert()["subjectAltName"]
                            return
                except OSError as exc:
                    last_error = exc
                    time.sleep(0.1)
            pytest.fail(f"TLS sem SNI não funcionou: {last_error}")
        finally:
            process.terminate()
            process.wait(timeout=5)
