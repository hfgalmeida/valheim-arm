#!/usr/bin/env python3
"""Root host service exposing only status and a fixed image update over a Unix socket."""
import argparse
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
import re
import shutil
import socket
import socketserver
import struct
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler

log = logging.getLogger("valheim-host-agent")
PHASES = {
    "reconciling": "Verifying the current installation without switching images.",
    "queued": "Update requested.",
    "downloading": "Baixando a imagem Docker.",
    "preflight": "Validando a nova imagem.",
    "stopping": "Stopping the game gracefully.",
    "backup": "Creating a backup before switching.",
    "switching": "Applying the new image; the panel may disconnect.",
    "verifying": "Checking the panel and desired game state.",
    "rollback": "Trying to restore the previous image.",
}


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".agent-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def update_lock(root):
    import fcntl

    file = (root / ".image-update.lock").open("a")
    try:
        fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BaseException:
        file.close()
        raise
    return file


def public_state(state):
    return {
        "available": True,
        **{
            key: state[key]
            for key in ("state", "phase", "message", "request_id", "started", "finished")
            if key in state
        },
    }


class Agent:
    def __init__(self, root, state_path, updater_factory, image="hfgalmeida/valheim-arm:latest"):
        self.root, self.path, self.factory, self.image = root, state_path, updater_factory, image
        self.mutex = threading.Lock()
        self.worker = None
        self.state = {"state": "idle", "message": "Ready to update through the panel."}
        if self.path.exists():
            try:
                state = json.loads(self.path.read_text())
                if not isinstance(state, dict) or state.get("state") not in (
                    "idle",
                    "reconciled",
                    "running",
                    "succeeded",
                    "failed",
                    "rolled_back",
                    "interrupted",
                ):
                    raise ValueError
                self.state = state
            except (OSError, ValueError):
                self.state = {
                    "state": "interrupted",
                    "message": "Agent history unavailable; check the host service.",
                }
        if self.state["state"] == "running":
            self.state.update(
                state="interrupted",
                message="The service was interrupted during the update. Check the server before trying again.",
                finished=time.time(),
            )
            atomic_json(self.path, self.state)

    def status(self):
        with self.mutex:
            return public_state(self.state)

    def progress(self, phase):
        with self.mutex:
            self.state.update(phase=phase, message=PHASES.get(phase, "Update in progress."))
            try:
                atomic_json(self.path, self.state)
            except OSError:
                log.exception("Could not persist progress; update will continue")

    def request(self, request_id):
        if not re.fullmatch(r"[a-f0-9]{32}", request_id):
            raise ValueError("Invalid request.")
        with self.mutex:
            if self.state.get("request_id") == request_id:
                return public_state(self.state)
            if self.state["state"] == "running":
                raise ValueError("An update is already in progress.")
            if time.time() - self.state.get("finished", 0) < 60:
                raise ValueError("Wait one minute before requesting another update.")
            try:
                lock = update_lock(self.root)
            except BlockingIOError:
                raise ValueError("Another update is running on the host.") from None
            state = {
                "state": "running",
                "phase": "queued",
                "message": PHASES["queued"],
                "request_id": request_id,
                "started": time.time(),
            }
            try:
                atomic_json(self.path, state)
            except BaseException:
                lock.close()
                raise
            self.state = state
            self.worker = threading.Thread(target=self.apply, args=(lock,), daemon=False)
            try:
                self.worker.start()
            except RuntimeError:
                lock.close()
                self.state.update(
                    state="failed",
                    message="Could not start the update.",
                    finished=time.time(),
                )
                atomic_json(self.path, self.state)
                raise OSError("Could not create worker") from None
            return public_state(state)

    def apply(self, lock):
        try:
            self.factory(self.root, progress=self.progress).update(self.image)
            outcome, message = (
                "succeeded",
                "Update complete; desired game state preserved.",
            )
        except Exception as exc:
            log.exception("Image update did not complete")
            outcome, message = (
                "failed",
                "Update did not complete. Check the host service logs.",
            )
            messages = {
                "readiness_timeout": "The game did not become ready before the deadline. Check game diagnostics and service logs.",
                "rollback_unverified": "The previous image was reapplied, but the game did not become ready. Complete recovery on the host before trying again.",
                "recovery_required": "A previous update is incomplete. Complete recovery on the host before trying again.",
            }
            message = messages.get(getattr(exc, "code", None), message)
            try:
                result = self.root / ".image-update.json"
                if (
                    json.loads(result.read_text()).get("phase") == "rolled-back"
                    and result.stat().st_mtime >= self.state["started"]
                ):
                    outcome, message = (
                        "rolled_back",
                        "Update failed; previous image restored.",
                    )
            except (OSError, ValueError):
                pass
        finally:
            lock.close()
        with self.mutex:
            self.state.update(state=outcome, message=message, finished=time.time())
            try:
                atomic_json(self.path, self.state)
            except OSError:
                log.exception("Could not write agent result")


def peer_allowed(connection):
    credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
    _, uid, _ = struct.unpack("3i", credentials)
    return uid in (0, 10001)


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def log_message(self, *args):
        pass

    def reply(self, status, value):
        payload = json.dumps(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if not peer_allowed(self.connection):
            return self.reply(403, {"detail": "Acesso negado."})
        if self.path != "/status":
            return self.reply(404, {"detail": "Operation unavailable."})
        self.reply(200, self.server.agent.status())

    def do_POST(self):
        if not peer_allowed(self.connection):
            return self.reply(403, {"detail": "Acesso negado."})
        if self.path != "/update":
            return self.reply(404, {"detail": "Operation unavailable."})
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 256 or self.headers.get("Transfer-Encoding"):
                raise ValueError("Invalid request body.")
            body = json.loads(self.rfile.read(length))
            if (
                not isinstance(body, dict)
                or set(body) != {"request_id"}
                or not isinstance(body["request_id"], str)
            ):
                raise ValueError("Invalid request.")
            self.reply(202, self.server.agent.request(body["request_id"]))
        except (ValueError, UnicodeError) as exc:
            self.reply(409, {"detail": str(exc) if type(exc) is ValueError else "Invalid request."})
        except OSError:
            self.reply(503, {"detail": "Could not record the update on the host."})


class Server(
    socketserver.ThreadingMixIn, getattr(socketserver, "UnixStreamServer", socketserver.BaseServer)
):
    daemon_threads = True

    def __init__(self, *args, **kwargs):
        if not hasattr(socketserver, "UnixStreamServer"):
            raise OSError("The updater agent requires Linux and Unix sockets.")
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


def compose_sources(root):
    env = root / ".env"
    if any(part.is_symlink() for part in (env, *env.parents)):
        raise ValueError("The .env file must not be a symbolic link.")
    text = env.read_text()
    matches = re.findall(r"(?m)^\s*(?:export\s+)?COMPOSE_FILE\s*=([^\r\n]*)$", text)
    if matches:
        value = matches[-1].strip().strip("\"'")
        if not re.fullmatch(r"[A-Za-z0-9_./ :+-]+", value):
            raise ValueError(
                "COMPOSE_FILE uses advanced syntax; simplify the file list before installing the agent."
            )
        files = value.split(":")
    else:
        files = ["compose.yaml"]
        for override in ("compose.override.yaml", "compose.override.yml"):
            if (root / override).exists():
                files.append(override)
                break
    files = [name for name in files if name != "compose.update-agent.yaml"]
    for name in files:
        path = root / name
        if (
            any(part.is_symlink() for part in (path, *path.parents))
            or not path.is_file()
            or not path.resolve().is_relative_to(root)
            or path.resolve().is_relative_to(root / "data")
        ):
            raise ValueError(
                "Compose files must be inside the installation directory, outside data, without symbolic links."
            )
        if re.search(r"(?m)^\s*(?:include|extends)\s*:", path.read_text()):
            raise ValueError("The agent requires local Compose files without include/extends.")
    return text, files


def install(root):
    if os.geteuid() != 0:
        raise ValueError("Install the agent with sudo.")
    root = root.absolute()
    if any(part.is_symlink() for part in (root, *root.parents)):
        raise ValueError("The installation directory must not contain symbolic links.")
    for parent in (root, *root.parents):
        stat = parent.stat()
        if (
            stat.st_uid == 10001
            and stat.st_mode & 0o200
            or stat.st_gid == 10001
            and stat.st_mode & 0o020
            or stat.st_mode & 0o002
        ):
            raise ValueError(
                "The installation directory and its parents must not be writable by the container UID/GID or by everyone."
            )
    for path in (root / "update-server.py", root / ".update-server.py.previous"):
        if path.is_symlink():
            raise ValueError("The local updater and its previous copy must not be symbolic links.")
    env_text, files = compose_sources(root)
    identity = hashlib.sha256(str(root).encode()).hexdigest()[:12]
    library = Path("/usr/local/lib/valheim-arm") / identity
    config_dir = Path("/etc/valheim-arm")
    runtime = root / ".update-agent"
    lock = update_lock(root)
    try:
        for path in (library, config_dir, runtime):
            path.mkdir(parents=True, exist_ok=True)
        for path in (library, config_dir):
            os.chown(path, 0, 0)
            path.chmod(0o700)
        os.chown(runtime, 0, 10001)
        runtime.chmod(0o750)
        for name in files:
            os.chown(root / name, 0, 0)
            (root / name).chmod(0o600)
        override = root / "compose.update-agent.yaml"
        if (
            override.exists()
            and override.read_text()
            != "services:\n  valheim:\n    volumes:\n      - ./.update-agent:/run/valheim-update:ro\n"
        ):
            raise ValueError(
                "compose.update-agent.yaml already exists with different content; preserve it and choose another filename before installing."
            )
        override.write_text(
            "services:\n  valheim:\n    volumes:\n      - ./.update-agent:/run/valheim-update:ro\n"
        )
        os.chown(override, 0, 0)
        override.chmod(0o600)
        files.append("compose.update-agent.yaml")
        lines = [
            line
            for line in env_text.splitlines()
            if not re.match(r"^\s*(?:export\s+)?COMPOSE_FILE\s*=", line)
        ]
        fd, temporary = tempfile.mkstemp(dir=root, prefix=".agent-env-")
        with os.fdopen(fd, "w") as output:
            output.write("\n".join(lines) + '\nCOMPOSE_FILE="' + ":".join(files) + '"\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, root / ".env")
        source = Path(__file__).resolve().parent
        for name in ("host-agent.py", "update-server.py"):
            shutil.copyfile(source / name, library / name)
            (library / name).chmod(0o600)
        # Keep the documented CLI consistent with the service's managed copy.
        local_cli = root / "update-server.py"
        content = (source / "update-server.py").read_bytes()
        if local_cli.exists() and local_cli.read_bytes() != content:
            previous_cli = root / ".update-server.py.previous"
            shutil.copyfile(local_cli, previous_cli)
            os.chown(previous_cli, 0, 0)
            previous_cli.chmod(0o600)
        if not local_cli.exists() or local_cli.read_bytes() != content:
            fd, temporary = tempfile.mkstemp(dir=root, prefix=".agent-cli-")
            try:
                with os.fdopen(fd, "wb") as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                os.replace(temporary, local_cli)
            finally:
                Path(temporary).unlink(missing_ok=True)
        os.chown(local_cli, 0, 0)
        local_cli.chmod(0o600)
        config = config_dir / (identity + ".json")
        atomic_json(
            config,
            {"root": str(root), "runtime": str(runtime), "image": "hfgalmeida/valheim-arm:latest"},
        )
        service = "valheim-arm-updater-" + identity + ".service"
        unit = (
            "[Unit]\nDescription=Valheim ARM image updater\nAfter=docker.service\nRequires=docker.service\n\n[Service]\nType=simple\nExecStart=/usr/bin/python3 "
            + str(library / "host-agent.py")
            + " serve --config "
            + str(config)
            + "\nRestart=on-failure\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n\n[Install]\nWantedBy=multi-user.target\n"
        )
        Path("/etc/systemd/system", service).write_text(unit)
        subprocess.run(["systemctl", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "enable", service], check=True)
        subprocess.run(["systemctl", "restart", service], check=True)
        print("Updater agent installed: " + service)
    finally:
        lock.close()


def serve(config_path):
    config = json.loads(config_path.read_text())
    root, runtime = Path(config["root"]), Path(config["runtime"])
    spec = importlib.util.spec_from_file_location(
        "managed_updater", Path(__file__).with_name("update-server.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    agent = Agent(root, runtime / "status.json", module.Updater, config["image"])
    path = runtime / "agent.sock"
    if path.exists():
        path.unlink()
    with Server(str(path), Handler) as server:
        server.agent = agent
        os.chown(path, 0, 10001)
        path.chmod(0o660)
        server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Valheim ARM local update agent")
    commands = parser.add_subparsers(dest="command", required=True)
    setup = commands.add_parser("install")
    setup.add_argument("--directory", type=Path, required=True)
    start = commands.add_parser("serve")
    start.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.basicConfig(level=logging.INFO)
    try:
        if args.command == "install":
            install(args.directory)
        else:
            serve(args.config)
    except (ValueError, OSError, subprocess.CalledProcessError) as exc:
        raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
