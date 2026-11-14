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


@pytest.mark.skipif(
    not CADDY, reason="Caddy necessário; configure CADDY_EXE para testar o TLS real"
)
def test_proxy_presents_public_ip_certificate_without_sni(tmp_path):
    ip = "192.0.2.10"
    source = Path(__file__).resolve().parents[1] / "docker/Caddyfile"
    caddyfile = tmp_path / "Caddyfile"
    # Tests must never install a certificate authority in the host trust store.
    caddyfile.write_text(source.read_text())
    env = os.environ | {
        "SERVER_IP": ip,
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    result = subprocess.run(
        [CADDY, "adapt", "--config", str(caddyfile), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        env=env,
    )
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
        process = subprocess.Popen(
            [CADDY, "run", "--config", str(config_path)],
            env=env,
            stdout=logs,
            stderr=logs,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
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


@pytest.mark.skipif(not CADDY, reason="Caddy necessário para validar os limites HTTPS")
def test_plugin_upload_has_a_separate_request_limit(tmp_path):
    import http.client
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Backend(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            try:
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"ok")
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    backend = ThreadingHTTPServer(("127.0.0.1", 0), Backend)
    thread = threading.Thread(target=backend.serve_forever, daemon=True)
    thread.start()
    env = os.environ | {
        "SERVER_IP": "192.0.2.10",
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
    }
    source = Path(__file__).resolve().parents[1] / "docker/Caddyfile"
    result = subprocess.run(
        [CADDY, "adapt", "--config", str(source), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        port = reserve.getsockname()[1]
    for server in config["apps"]["http"]["servers"].values():
        server["listen"] = [f"127.0.0.1:{port}"]

    def local_backend(node):
        if isinstance(node, dict):
            if node.get("handler") == "reverse_proxy":
                node["upstreams"] = [{"dial": f"127.0.0.1:{backend.server_port}"}]
            for value in node.values():
                local_backend(value)
        elif isinstance(node, list):
            for value in node:
                local_backend(value)

    local_backend(config)
    storage = tmp_path / "storage"
    config["storage"] = {"module": "file_system", "root": str(storage)}
    path = tmp_path / "caddy.json"
    path.write_text(json.dumps(config))
    with (tmp_path / "caddy.log").open("w") as logs:
        process = subprocess.Popen(
            [CADDY, "run", "--config", str(path)],
            env=env,
            stdout=logs,
            stderr=logs,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        try:
            deadline = time.monotonic() + 10
            while not (storage / "pki/authorities/local/root.crt").exists():
                assert process.poll() is None and time.monotonic() < deadline
                time.sleep(0.05)
            context = ssl.create_default_context(
                cafile=str(storage / "pki/authorities/local/root.crt")
            )
            context.check_hostname = False
            while True:
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=1) as ready:
                        with context.wrap_socket(ready, server_hostname=None):
                            break
                except OSError:
                    assert time.monotonic() < deadline
                    time.sleep(0.05)
            for endpoint, status in [
                ("/api/v1/plugins?name=test.dll", 200),
                ("/api/v1/mod-config", 413),
                ("/api/v1/plugins/restore", 413),
            ]:
                connection = http.client.HTTPSConnection(
                    "127.0.0.1", port, context=context, timeout=5
                )
                connection.request(
                    "POST",
                    endpoint,
                    body=b"x" * (96 * 1024),
                    headers={"Host": "192.0.2.10", "Connection": "close"},
                )
                response = connection.getresponse()
                assert response.status == status, (endpoint, response.status)
                response.read()
                connection.close()
        finally:
            process.terminate()
            process.wait(timeout=5)
            backend.shutdown()
            backend.server_close()
            thread.join(timeout=2)
