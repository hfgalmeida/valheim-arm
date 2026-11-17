#!/usr/bin/env python3
"""Host-only image updater. No Docker socket is exposed to the panel."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time


BACKUP_CODE = """
import fcntl
from pathlib import Path
from app.backups import Backups
from app.config import ConfigStore
root = Path('/data')
with (root / 'config/supervisor.lock').open('a') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if (root / 'config/game-active.json').exists():
        raise SystemExit('Clean shutdown not confirmed; update cancelled.')
    print(Backups(root).create('image-update', ConfigStore(root).effective()))
"""
HEALTH_CODE = "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=3))))"


class UpdateFailure(RuntimeError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def atomic_write(path, content):
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".update-")
    try:
        with os.fdopen(fd, "w") as output:
            output.write(content)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def set_image(path, image):
    # Do not source shell code or rewrite other .env settings.
    lines = path.read_text().splitlines()
    lines = [line for line in lines if not re.match(r"^\s*(?:export\s+)?VALHEIM_IMAGE\s*=", line)]
    atomic_write(path, "\n".join(lines) + "\nVALHEIM_IMAGE=" + image + "\n")


class Updater:
    def __init__(self, root, timeout=900, progress=None):
        self.root, self.timeout = root, timeout
        self.progress = progress or (lambda phase: None)
        self.env = os.environ.copy()
        self.env.pop("VALHEIM_IMAGE", None)
        self.state = root / ".image-update.json"

    def run(self, *args, capture=False):
        result = subprocess.run(
            ["docker", *args],
            cwd=self.root,
            env=self.env,
            text=True,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.PIPE if capture else None,
            timeout=2400,
        )
        if result.returncode:
            raise RuntimeError("Docker command failed: " + " ".join(args[:2]))
        return result.stdout.strip() if capture else ""

    def compose(self, *args, **kwargs):
        return self.run("compose", *args, **kwargs)

    def save(self, state):
        atomic_write(self.state, json.dumps(state))

    def desired_running(self):
        path = self.root / "data/config/run-intent.json"
        try:
            value = json.loads(path.read_text())
        except FileNotFoundError:
            return True
        except (OSError, ValueError):
            raise RuntimeError(
                "Invalid run preference; correct it in the panel before updating."
            ) from None
        if (
            not isinstance(value, dict)
            or type(value.get("version")) is not int
            or value.get("version") != 1
            or type(value.get("running")) is not bool
        ):
            raise RuntimeError("Invalid run preference; correct it in the panel before updating.")
        return value["running"]

    def require_intent_support(self, image):
        if self.desired_running():
            return
        try:
            self.run(
                "run",
                "--rm",
                "--network",
                "none",
                "--entrypoint",
                "/opt/venv/bin/python",
                image,
                "-c",
                "import importlib.util,sys;sys.exit(0 if importlib.util.find_spec('app.run_intent') else 1)",
                capture=True,
            )
        except RuntimeError:
            raise RuntimeError(
                "The selected image cannot keep the game stopped. Switch refused before stopping the container; use a compatible image or start the game before rolling back."
            ) from None

    def wait_ready(self):
        deadline = time.monotonic() + self.timeout
        stable_since = None
        last_detail = None
        while time.monotonic() < deadline:
            expected_running = self.desired_running()
            try:
                status = json.loads(
                    self.compose(
                        "exec",
                        "-T",
                        "valheim",
                        "/opt/venv/bin/python",
                        "-c",
                        HEALTH_CODE,
                        capture=True,
                    )
                )
                game = {
                    "parado": "stopped",
                    "iniciando": "starting",
                    "falha": "failed",
                    "parando": "stopping",
                    "instalando": "installing",
                }.get(status.get("game"), status.get("game"))
                game = (
                    game
                    if game in ("online", "stopped", "starting", "failed", "stopping", "installing")
                    else "unknown"
                )
                detail = (
                    "Check: game="
                    + game
                    + "; expected="
                    + ("online" if expected_running else "stopped")
                )
                if detail != last_detail:
                    print(detail, flush=True)
                    last_detail = detail
                ready = (
                    status.get("supervisor") == "ok"
                    and not status.get("run_intent_error")
                    and (
                        game == "online"
                        if expected_running
                        else game == "stopped" and status.get("desired_running") is False
                    )
                )
                if ready:
                    stable_since = stable_since or time.monotonic()
                    if time.monotonic() - stable_since >= 30:
                        last_detail = "Game stable; checking the HTTPS proxy"
                        print(last_detail, flush=True)
                        self.compose(
                            "exec",
                            "-T",
                            "proxy",
                            "/usr/bin/caddy",
                            "validate",
                            "--config",
                            "/etc/caddy/Caddyfile",
                            "--adapter",
                            "caddyfile",
                            capture=True,
                        )
                        return
                else:
                    stable_since = None
            except (RuntimeError, ValueError):
                stable_since = None
            time.sleep(5)
        raise UpdateFailure(
            "The game did not become ready and stable in the requested state before the deadline. "
            + (last_detail or "Panel or health check unavailable."),
            "readiness_timeout",
        )

    def deployment_identity(self):
        model = json.loads(self.compose("config", "--format", "json", capture=True))
        result = []
        for service in ("valheim", "proxy"):
            container = self.compose("ps", "--quiet", service, capture=True)
            if not container:
                raise UpdateFailure(
                    "Expected container is not running; reconciliation refused.",
                    "recovery_required",
                )
            info = json.loads(self.run("inspect", container, capture=True))[0]
            reference = model["services"][service]["image"]
            expected = json.loads(self.run("image", "inspect", reference, capture=True))[0]["Id"]
            if not info.get("State", {}).get("Running") or info["Image"] != expected:
                raise UpdateFailure(
                    "Running image differs from configuration; reconciliation refused.",
                    "recovery_required",
                )
            result.append((service, info["Id"], info["Image"]))
        if result[0][2] != result[1][2]:
            raise UpdateFailure(
                "Valheim and proxy use different images; reconciliation refused.",
                "recovery_required",
            )
        return result

    def reconcile(self):
        if not self.state.exists():
            return False
        state = json.loads(self.state.read_text())
        if state.get("phase") not in (
            "prepared",
            "switching",
            "rollback-verifying",
            "rollback-unverified",
        ):
            return False
        if not state.get("previous") or not state.get("target"):
            raise UpdateFailure(
                "Incomplete recovery record; reconciliation refused.", "recovery_required"
            )
        status_path = self.root / ".update-agent/status.json"
        status = json.loads(status_path.read_text()) if status_path.exists() else None
        if status and status.get("state") == "running":
            raise UpdateFailure(
                "The agent reports an operation in progress; check the host.", "recovery_required"
            )
        self.progress("reconciling")
        identity = self.deployment_identity()
        self.wait_ready()
        if identity != self.deployment_identity():
            raise UpdateFailure(
                "The installation changed during verification; reconciliation refused.",
                "recovery_required",
            )
        old = dict(state)
        archive = self.root / (".image-update-recovery-" + str(time.time_ns()) + ".json")
        atomic_write(archive, json.dumps(old))
        state.update(
            phase="reconciled",
            previous_phase=old["phase"],
            verified_image=identity[0][2],
            reconciled_at=time.time(),
            rollback_eligible=False,
        )
        self.save(state)
        if status is not None:
            status.update(
                state="reconciled",
                phase="reconciling",
                finished=time.time(),
                message="Old state closed after verifying the current installation. Ready for another update; no image was switched during this check.",
            )
            atomic_write(status_path, json.dumps(status, ensure_ascii=False))
        print(
            "Current installation verified; old record reconciled without restarting containers.",
            flush=True,
        )
        return True

    def restore(self, state):
        if state.get("rollback_eligible") is False:
            raise UpdateFailure(
                "The old record was reconciled; it does not represent a rollback of the current installation.",
                "recovery_required",
            )
        self.progress("rollback")
        self.run("image", "inspect", state["previous"], capture=True)
        self.require_intent_support(state["previous"])
        self.compose("stop")
        # Keep the newer saves. Never restore an older world automatically.
        set_image(self.root / ".env", state["previous"])
        self.compose("up", "-d", "--no-build", "--pull", "never")
        state["phase"] = "rollback-verifying"
        self.save(state)
        try:
            self.wait_ready()
        except Exception as exc:
            state["phase"] = "rollback-unverified"
            self.save(state)
            raise UpdateFailure(
                "Previous image reapplied, but readiness was not confirmed. Recovery must be completed on the host.",
                "rollback_unverified",
            ) from exc
        state["phase"] = "rolled-back"
        self.save(state)
        print("Previous image restored. Saves and settings preserved.")

    def update(self, image, rollback=False):
        if rollback:
            if not self.state.exists():
                raise RuntimeError("No previous update is recorded.")
            self.restore(json.loads(self.state.read_text()))
            return
        if self.state.exists() and json.loads(self.state.read_text()).get("phase") in (
            "prepared",
            "switching",
            "rollback-verifying",
            "rollback-unverified",
        ):
            raise UpdateFailure(
                "Update interrupted. Run --rollback before trying again.",
                "recovery_required",
            )
        self.desired_running()
        container = self.compose("ps", "--quiet", "valheim", capture=True)
        if not container:
            raise RuntimeError("Start the existing installation before updating.")
        previous_info = json.loads(self.run("inspect", container, capture=True))[0]
        previous_id = previous_info["Image"]
        world = next(
            (
                value
                for value in previous_info.get("Config", {}).get("Env", [])
                if value.startswith("WORLD_NAME=")
            ),
            "WORLD_NAME=Dedicated",
        )
        if shutil.disk_usage(self.root / "data").free < 6 * 1024**3:
            raise RuntimeError("Free at least 6 GiB on the data disk before updating.")
        # Fetch and preflight while the current world is still available.
        self.progress("downloading")
        self.run("pull", "--platform", "linux/arm64", image)
        target_id = json.loads(self.run("image", "inspect", image, capture=True))[0]["Id"]
        if previous_id == target_id:
            print("The requested image is already installed. No interruption needed.")
            return
        self.progress("preflight")
        self.require_intent_support(target_id)
        self.run(
            "run",
            "--rm",
            "--platform",
            "linux/arm64",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "DATA_DIR=/tmp/valheim",
            "-e",
            "HOME=/tmp/valheim/home",
            "-e",
            "FEX_ROOTFS=/opt/rootfs",
            target_id,
            "preflight",
        )
        self.run(
            "run",
            "--rm",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "-e",
            "SERVER_IP=192.0.2.1",
            "-e",
            "XDG_DATA_HOME=/tmp/caddy-data",
            "-e",
            "XDG_CONFIG_HOME=/tmp/caddy-config",
            "--entrypoint",
            "/usr/bin/caddy",
            target_id,
            "validate",
            "--config",
            "/etc/caddy/Caddyfile",
            "--adapter",
            "caddyfile",
        )
        previous = "valheim-arm-local:previous-" + previous_id.split(":")[-1][:16]
        target = "valheim-arm-local:current-" + target_id.split(":")[-1][:16]
        self.run("tag", previous_id, previous)
        self.run("tag", target_id, target)
        # Save recovery information before the first interruption.
        state = {"previous": previous, "target": target, "source": image, "phase": "prepared"}
        self.save(state)
        self.progress("stopping")
        self.compose("stop", "valheim")
        try:
            self.progress("backup")
            # Run the backup with the exact OLD image (also supports existing releases).
            self.run(
                "run",
                "--rm",
                "--network",
                "none",
                "--user",
                "10001:10001",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges:true",
                "--mount",
                "type=bind,src=" + str(self.root / "data") + ",dst=/data",
                "-e",
                "SERVER_PASSWORD=image-update-placeholder",
                "-e",
                world,
                "--entrypoint",
                "/opt/venv/bin/python",
                previous_id,
                "-c",
                BACKUP_CODE,
            )
        except Exception:
            self.compose("start", "valheim")
            state["phase"] = "aborted"
            self.save(state)
            raise RuntimeError(
                "Backup not confirmed; current image preserved. Check the logs."
            ) from None
        state["phase"] = "switching"
        self.save(state)
        try:
            self.progress("switching")
            set_image(self.root / ".env", target)
            self.compose("up", "-d", "--no-build", "--pull", "never")
            self.progress("verifying")
            self.wait_ready()
        except (Exception, KeyboardInterrupt):
            print("New image verification failed. Attempting to restore the previous image…")
            self.restore(state)
            raise RuntimeError("Update cancelled; previous image restored.") from None
        state["phase"] = "completed"
        self.save(state)
        print("Update complete. Panel verified; desired game state preserved.")
        print("Manual rollback: sudo python3 update-server.py --rollback")


def main():
    parser = argparse.ArgumentParser(
        description="Update the Valheim image with backup and automatic rollback."
    )
    parser.add_argument("--image", default="hfgalmeida/valheim-arm:latest")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument(
        "--reconcile",
        action="store_true",
        help="Verify the current installation and close an old record without restarting containers",
    )
    parser.add_argument(
        "--directory", type=Path, help="Installation directory used by the host agent"
    )
    parser.add_argument(
        "--timeout", type=int, default=900, help="Startup timeout in seconds (default: 900)."
    )
    args = parser.parse_args()
    if args.timeout < 60:
        parser.error("--timeout must be at least 60")
    import fcntl

    if args.rollback and args.reconcile:
        parser.error("Escolha --rollback ou --reconcile")
    root = args.directory.resolve() if args.directory else Path(__file__).resolve().parent
    os.umask(0o077)
    if not (root / ".env").is_file() or not (root / "compose.yaml").is_file():
        parser.error(
            "Run the script in the installation directory containing compose.yaml and .env."
        )
    with (root / ".image-update.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            parser.error("Another update is already in progress.")
        try:
            updater = Updater(root, args.timeout)
            if args.reconcile:
                updater.reconcile()
            else:
                updater.update(args.image, args.rollback)
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as exc:
            raise SystemExit(str(exc)) from None


if __name__ == "__main__":
    main()
