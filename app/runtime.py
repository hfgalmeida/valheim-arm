import asyncio
import json
import logging
import os
import platform
import re
import shutil
import signal
import subprocess
import tarfile
import tempfile
import time
import urllib.request
import uuid
import zipfile
from collections import deque
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .maintenance_history import MaintenanceHistory, origin, prune
from .host_updates import HostUpdates
from .run_intent import RunIntent
from .startup_watchdog import StartupWatchdog, world_ready
from .startup_diagnostics import StartupDiagnostics
from .game_logs import read_game_line
from .image_version import ImageVersion
from .mod_diagnostics import ModDiagnostics
from .audit import Audit
from .alerts import Alerts
from .backups import Backups, digest
from .cloud import Cloud
from .file_access import FileAccess
from .config import ConfigStore, atomic_json
from .compatibility import operation_record
from .players import Players, query_steam
from .player_directory import PlayerDirectory

log = logging.getLogger("valheim")
GAME_STOP_TIMEOUT = 150
STARTUP_FATAL_GRACE = 30
STARTUP_KILL_TIMEOUT = 10
SHUTDOWN_JOB_TIMEOUT = 120
STOP_PROGRESS_INTERVAL = 15
LOG_DRAIN_TIMEOUT = 2
MAX_RECOVERY_FAILURES = 5
STABLE_SESSION_SECONDS = 600


class JobCancelled(RuntimeError):
    """An account was revoked before its queued action could run."""


class MaintenanceDeferred(RuntimeError):
    """Safety checks refused maintenance before any stop signal was sent."""


class SteamError(RuntimeError):
    def __init__(self, code, output):
        self.code, self.output = code, output
        super().__init__(
            f"SteamCMD failed (exit code {code}); check the logs and /data/home/Steam/logs/stderr.txt"
        )


def save_completed(line):
    # Legacy saves and the final commit of the chunked save format. Earlier
    # stages (even DB2/FWL written) do not confirm a complete world save.
    return bool(
        re.search(r"\bWorld saved\b|\bWorld save \(5/5\) done\.", line)
        and not re.search(r"\b(?:error|failed|exception)\b", line, re.IGNORECASE)
    )


def public_build(output):
    match = re.search(r'"896660"\s*\{', output)
    if not match:
        raise RuntimeError("Application metadata not found")
    tokens = iter(re.findall(r'"([^"\n]*)"|([{}])', output[match.end() :]))

    def block():
        result = {}
        for string, brace in tokens:
            if brace == "}":
                return result
            if brace:
                raise ValueError("Invalid Steam metadata")
            key = string
            value, mark = next(tokens)
            result[key] = block() if mark == "{" else value
        raise ValueError("Incomplete Steam metadata")

    try:
        value = block()["depots"]["branches"]["public"]["buildid"]
        if not value.isdigit():
            raise ValueError("Invalid build")
        return value
    except (KeyError, StopIteration, ValueError) as exc:
        raise RuntimeError("Public build not found; update deferred") from exc


def preflight(data: Path):
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise RuntimeError("Run this image on a Linux ARM64 host")
    kernel = tuple(map(int, platform.release().split("-")[0].split(".")[:2]))
    if kernel < (5, 15) or os.sysconf("SC_PAGE_SIZE") != 4096:
        raise RuntimeError("This image requires kernel >=5.15 and 4 KiB memory pages")
    features = Path("/proc/cpuinfo").read_text()
    if not all(feature in features for feature in ("atomics", "crc32", "asimd", "dcpop")):
        raise RuntimeError("This image requires an ARMv8.2 CPU with atomics/CRC/ASIMD")
    if not shutil.which("FEXInterpreter") or not Path("/opt/rootfs/usr/bin/env").exists():
        raise RuntimeError("FEX or rootfs missing; rebuild the image")
    data.mkdir(parents=True, exist_ok=True)
    (data / "home").mkdir(exist_ok=True)
    (data / "cache").mkdir(exist_ok=True)
    with tempfile.TemporaryFile(dir=data):
        pass
    if shutil.disk_usage(data).free < 6 * 1024**3:
        raise RuntimeError("At least 6 GiB of free space is required for installation and updates")
    result = subprocess.run(
        ["FEXInterpreter", "/usr/bin/env", "VALHEIM_PREFLIGHT=1", "/usr/bin/uname", "-m"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode or "x86_64" not in result.stdout:
        raise RuntimeError(
            "FEX smoke test failed; check kernel, rootfs, and logs: " + result.stderr[-1000:]
        )


def guest_library_path(install):
    root = Path(os.environ.get("FEX_ROOTFS", "/opt/rootfs"))
    return ":".join(
        map(
            str,
            (
                install / "linux64",
                install,
                install / "valheim_server_Data/Plugins",
                install / "valheim_server_Data/Plugins/x86_64",
                root / "usr/lib/x86_64-linux-gnu",
                root / "lib/x86_64-linux-gnu",
                root / "usr/lib/x86_64-linux-gnu/pulseaudio",
            ),
        )
    )


def check_crossplay(install):
    root = Path(os.environ.get("FEX_ROOTFS", "/opt/rootfs"))
    loader = root / "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    libraries = sorted(
        p
        for p in (install / "valheim_server_Data/Plugins").rglob("*.so")
        if p.name.lower() == "libparty.so"
    )
    if not libraries:
        raise RuntimeError("Crossplay: Party library not found in the Valheim installation")
    env = os.environ.copy()
    env["FEX_ROOTFS"] = str(root)
    env.pop("LD_TRACE_LOADED_OBJECTS", None)
    for library in libraries:
        paths = guest_library_path(install) + ":" + str(library.parent)
        result = subprocess.run(
            ["FEXInterpreter", str(loader), "--library-path", paths, "--list", str(library)],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            raise RuntimeError(
                "Crossplay: missing or incompatible x86 dependency; update the Docker image. "
                + result.stderr[-2000:]
            )


def download(url, target, limit=256 * 1024**2):
    if not url.startswith("https://"):
        raise ValueError("Download exige HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "valheim-arm"})
    with urllib.request.urlopen(request, timeout=60) as source, target.open("wb") as output:
        if not source.url.startswith("https://"):
            raise ValueError("Redirect to an insecure protocol")
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("Download exceeds the limit")
            output.write(chunk)


def install_steam(data):
    steam = data / "steamcmd"
    executable = steam / "linux32/steamcmd"
    if executable.exists():
        return
    steam.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=data) as tmp:
        archive = Path(tmp) / "steamcmd.tar.gz"
        download("https://steamcdn-a.akamaihd.net/client/installer/steamcmd_linux.tar.gz", archive)
        with tarfile.open(archive) as pack:
            pack.extractall(steam, filter="data")
    if not executable.exists():
        raise RuntimeError("Incomplete SteamCMD installation")


BEPINEX_BUNDLE = Path("/opt/bepinex")


def install_bepinex(data, install, settings):
    if not settings.bepinex:
        return
    mods = data / "mods"
    marker = mods / "pack.json"
    for target in (
        marker,
        mods / "BepInEx/core",
        mods / "BepInEx/config/BepInEx.cfg",
        mods / "libdoorstop.so",
    ):
        if any(part.is_symlink() for part in (target, *target.parents)):
            raise ValueError("Symbolic links are not allowed in managed BepInEx paths")
    bundled = not settings.bepinex_url
    metadata = json.loads((BEPINEX_BUNDLE / "metadata.json").read_text()) if bundled else {}
    expected = metadata["sha256"] if bundled else settings.bepinex_sha256.lower()
    try:
        current = json.loads(marker.read_text()) if marker.exists() else {}
    except (OSError, ValueError):
        current = {}
    complete = (mods / "BepInEx/core/BepInEx.Preloader.dll").is_file() and (
        mods / "libdoorstop.so"
    ).is_file()
    if bundled:
        complete = complete and (mods / "BepInEx/config/BepInEx.cfg").is_file()
    if current.get("sha256") != expected or not complete:
        with tempfile.TemporaryDirectory(dir=data) as tmp:
            archive = Path(tmp) / "bepinex.zip"
            if bundled:
                shutil.copy2(BEPINEX_BUNDLE / "pack.zip", archive)
            else:
                download(settings.bepinex_url, archive)
            if digest(archive) != expected:
                raise ValueError("BepInEx SHA256 mismatch")
            unpack = Path(tmp) / "unpack"
            unpack.mkdir()
            with zipfile.ZipFile(archive) as pack:
                if sum(p.file_size for p in pack.infolist()) > 1024**3:
                    raise ValueError("BepInEx package too large")
                for item in pack.infolist():
                    parts = Path(item.filename).parts
                    if (
                        ".." in parts
                        or item.filename.startswith(("/", "\\"))
                        or "\\" in item.filename
                        or ":" in item.filename
                    ):
                        raise ValueError("Unsafe path in the BepInEx package")
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Links are not allowed in the BepInEx package")
                pack.extractall(unpack)
            roots = []
            for library in (*unpack.rglob("libdoorstop.so"), *unpack.rglob("libdoorstop_x64.so")):
                root = (
                    library.parent.parent
                    if library.parent.name == "doorstop_libs"
                    else library.parent
                )
                if (root / "BepInEx/core").is_dir():
                    roots.append((root, library))
            if len(roots) != 1:
                raise ValueError("Use Linux BepInExPack_Valheim with Doorstop x64 and BepInEx/core")
            source, library = roots[0]
            if not all(
                (source / "BepInEx/core" / name).is_file()
                for name in ("BepInEx.dll", "BepInEx.Preloader.dll")
            ):
                raise ValueError("Incomplete BepInEx package")
            for directory in ("plugins", "config", "patchers"):
                (mods / "BepInEx" / directory).mkdir(parents=True, exist_ok=True)
            core = mods / "BepInEx/core"
            if core.is_symlink():
                raise ValueError("BepInEx/core must not be a symbolic link")
            shutil.rmtree(core, ignore_errors=True)
            shutil.copytree(source / "BepInEx/core", core)
            default_config = source / "BepInEx/config/BepInEx.cfg"
            saved_config = mods / "BepInEx/config/BepInEx.cfg"
            if default_config.is_file() and not saved_config.exists():
                shutil.copy2(default_config, saved_config)
            shutil.copy2(library, mods / "libdoorstop.so")
            atomic_json(
                marker,
                {
                    "sha256": expected,
                    "source": "bundled" if bundled else "custom",
                    "version": metadata.get("version"),
                    "url": metadata.get("url") if bundled else settings.bepinex_url,
                },
            )
    for name, destination in (
        ("BepInEx", mods / "BepInEx"),
        ("libdoorstop.so", mods / "libdoorstop.so"),
    ):
        link = install / name
        if link.is_symlink():
            link.unlink()
        if link.exists():
            raise ValueError("Installation contains BepInEx outside its managed directory")
        link.symlink_to(destination, target_is_directory=name == "BepInEx")


class Runtime:
    def __init__(self, data: Path, config=None):
        self.data = data
        for directory in ("config", "saves", "home", "cache", "installs", "mods"):
            (data / directory).mkdir(parents=True, exist_ok=True)
        self.config = config or ConfigStore(data)
        self.audit = Audit(data)
        self.account_authorizer = None
        self.host_updates = HostUpdates()
        self.host_update_task = None
        self.backups = Backups(data)
        self.cloud = Cloud(data, self.backups)
        self.image_version = ImageVersion()
        self.image_version_task = None
        self.alerts = Alerts(data)
        self.alert_task = None
        self.cloud_task = None
        self.file_access = FileAccess(data)
        self.access_task = None
        self.mod_diagnostics = ModDiagnostics(data)
        self.players = Players()
        self.player_directory = PlayerDirectory(data / "config/known-players.json")
        self.startup_watchdog = StartupWatchdog()
        self.startup_diagnostics = StartupDiagnostics(data)
        self.process = None
        self.reader = None
        self.running = None
        self.join_code = None
        self.game_ready = False
        self.world_loaded = False
        self.transport_ready = False
        self.startup_failures = 0
        self.ready_since = None
        self.recovery_error = None
        self.last_failure = None
        self.schedule_started = datetime.now(ZoneInfo(self.config.effective().timezone))
        self.state = "stopped"
        self.logs = deque(maxlen=500)
        self.jobs = deque()
        self.maintenance_history = MaintenanceHistory(data)
        self.next_history_prune = time.monotonic() + 3600
        self.queue = asyncio.Queue(maxsize=32)
        self.active = None
        self.closing = False
        self.lock_file = None
        self.worker_task = None
        self.scheduler_task = None
        self.latest_build = None
        self.failed_update_build = None
        self.next_retry = 0
        self.run_intent = RunIntent(data)
        self.want_running = self.run_intent.running
        self.next_recovery = 0
        self.persistence_error = None
        self.last_save = 0
        self.stop_request = None
        self.clean_marker = data / "config/game-active.json"
        self.clean_stop = not self.clean_marker.exists()
        self.daily_path = data / "config/schedule.json"
        self.daily = json.loads(self.daily_path.read_text()) if self.daily_path.exists() else {}
        self.pending_path = data / "config/maintenance.json"
        for job in json.loads(self.pending_path.read_text()) if self.pending_path.exists() else []:
            job = operation_record(job)
            self.jobs.append(job)
            if job["state"] == "running":
                job["state"] = "failed"
                job["error"] = (
                    "Maintenance interrupted by the supervisor; it will not repeat automatically"
                )
                self.emit(job["error"])
            elif job["state"] in ("queued", "deferred"):
                if not self.run_intent.running:
                    job["state"] = "cancelled"
                    continue
                job["state"] = "queued"
                self.queue.put_nowait(job)

        pending_ids = {job["id"] for job in self.jobs}
        for job in self.maintenance_history.load():
            if job["id"] in pending_ids:
                continue
            if job["state"] in ("queued", "deferred", "running"):
                job["state"] = "failed" if job["state"] == "running" else "cancelled"
                job["error"] = (
                    "Operation interrupted by the supervisor; it will not repeat automatically"
                )
                job["finished"] = time.time()
            self.jobs.append(job)
        self.jobs = deque(sorted(self.jobs, key=lambda job: job.get("created", 0)))
        self.audit.seed(self.jobs)
        self.persist_history()

    @property
    def live(self):
        return self.process is not None and self.process.returncode is None

    def current_install(self):
        link = self.data / "current"
        return link.resolve() if link.exists() else None

    def installed_build(self):
        install = self.current_install()
        if install:
            files = list(install.rglob("appmanifest_896660.acf"))
            if files:
                match = re.search(r'"buildid"\s+"(\d+)"', files[0].read_text())
                if match:
                    return match[1]
        return None

    def emit(self, message):
        # Redact known secrets from subprocess output and errors.
        for password in {
            self.config.effective().server_password,
            self.running.server_password if self.running else "",
        }:
            if password:
                message = message.replace(password, "[password]")
        self.logs.append({"time": time.time(), "message": message[:4000]})
        log.info("%s", message[:4000])

    def record_failure(self, source, message):
        for password in {
            self.config.effective().server_password,
            self.running.server_password if self.running else "",
        }:
            if password:
                message = message.replace(password, "[password]")
        self.last_failure = {"time": time.time(), "source": source, "message": message[:1000]}

    def diagnostics(self):
        retry = None
        if self.closing:
            phase, detail = "stopping", "Supervisor encerrando."
        elif self.state == "stopping":
            phase, detail = "stopping", "Waiting for the game to save and exit."
        elif self.live and self.startup_watchdog.fatal:
            phase = (
                "recovering"
                if self.active and self.active["action"] == "recover-startup"
                else "failed"
            )
            detail = (
                self.recovery_error
                or "Fatal signal detected; waiting for controlled startup recovery."
            )
        elif self.live:
            if self.game_ready:
                phase, detail = "ready", "World loaded and server connection initialized."
            elif not self.world_loaded:
                phase, detail = "starting", "Waiting for the world to load."
            else:
                phase, detail = "starting", "World loaded; waiting for the server connection."
        elif self.active and self.active["action"] in ("start", "restart", "update", "restore"):
            phase, detail = "starting", "Preparing the game: " + self.state + "."
        elif self.run_intent.error:
            phase, detail = "failed", self.run_intent.error
        elif self.recovery_error:
            phase, detail = "failed", self.recovery_error
        elif self.want_running and self.next_recovery > 0:
            phase, detail = "recovering", "Automatic startup retry scheduled."
            retry = max(0, int(self.next_recovery - time.monotonic()))
        elif self.state == "failed":
            phase, detail = (
                "failed",
                "The game failed; check the last failure and logs.",
            )
        else:
            phase, detail = "stopped", "Game stopped."
        return {
            "phase": phase,
            "detail": detail,
            "retry_in_seconds": retry,
            "last_failure": self.last_failure,
        }

    def status(self):
        crossplay = (
            self.running if self.live and self.running else self.config.effective()
        ).crossplay
        return {
            "diagnostics": self.diagnostics(),
            "startup": self.startup_diagnostics.describe(),
            "mods": self.mod_diagnostics.describe(self.live, self.config.effective().bepinex),
            "state": self.state,
            "running": self.live,
            "players": (self.players.status() if self.live else self.players.stopped_status()),
            "crossplay": crossplay,
            "join_code": self.join_code if self.live and crossplay else None,
            "pending_config": self.running is not None and self.running != self.config.effective(),
            "installed_build": self.installed_build(),
            "available_build": self.latest_build,
            "maintenance_persistence_error": self.persistence_error,
            "maintenance_history_error": self.maintenance_history.error,
            "audit_error": self.audit.error,
            "automatic_recovery": self.want_running
            and self.run_intent.running
            and not self.closing,
            "run_intent": self.run_intent.describe(),
            "recovery_error": self.recovery_error,
            "image_version": self.image_version.describe(),
            "alerts": self.alerts.describe(),
            "cloud": self.cloud.describe(),
            "jobs": list(self.jobs),
            "logs": list(self.logs),
        }

    def submit(
        self, action, backup=None, actor=None, require_idle=False, request_id=None, credential=None
    ):
        if action not in ("start", "stop", "restart", "update", "backup", "restore"):
            raise ValueError("Unknown action")
        if action == "restore":
            self.backups.resolve(backup or "")
        operator_start = action == "start" and (actor or {}).get("role") == "operator"
        for pending in self.jobs:
            if (
                not pending.get("scheduled")
                and pending.get("actor", {}).get("id") == (actor or {}).get("id")
                and pending.get("actor_credential") == credential
                and bool(pending.get("requires_idle")) == require_idle
                and pending["action"] == action
                and pending.get("backup") == backup
                and (
                    pending["state"] in ("queued", "running")
                    or (require_idle and pending["state"] == "deferred")
                )
                and (
                    action not in ("start", "stop")
                    or require_idle
                    or operator_start
                    or pending.get("intent_revision") == self.run_intent.revision
                )
            ):
                return pending
        if self.queue.full():
            raise asyncio.QueueFull
        revision = None
        if (
            action in ("start", "stop")
            and not operator_start
            and not (require_idle and action == "stop")
        ):
            revision = self.set_run_intent(action == "start")
            for pending in self.jobs:
                if pending["action"] in ("start", "stop") and pending["state"] in (
                    "queued",
                    "deferred",
                ):
                    pending["state"] = "cancelled"
        job = {
            "id": uuid.uuid4().hex,
            "action": action,
            "backup": backup,
            "state": "queued",
            "created": time.time(),
        }
        if actor:
            job.update(actor=actor, actor_credential=credential, audit_request_id=request_id)
        if require_idle:
            job["requires_idle"] = True
        if revision is not None:
            job["intent_revision"] = revision
        self.queue.put_nowait(job)
        self.jobs.append(job)
        self.persist_pending()
        return job

    def trim_jobs(self):
        self.jobs = deque(prune(list(self.jobs)))

    def persist_history(self):
        now = time.time()
        secrets = (
            self.config.effective().server_password,
            self.running.server_password if self.running else "",
        )
        for job in self.jobs:
            job.setdefault("origin", origin(job))
            if job["state"] not in ("queued", "running", "deferred"):
                job.setdefault("finished", now)
            for field in ("error", "result"):
                if isinstance(job.get(field), str):
                    for secret in secrets:
                        if secret:
                            job[field] = job[field].replace(secret, "[password]")
                    job[field] = job[field][:1000]
        self.trim_jobs()
        self.maintenance_history.save(self.jobs, secrets)
        self.audit.sync_jobs(self.jobs)

    def persist_pending(self):
        self.persist_history()
        try:
            atomic_json(
                self.pending_path,
                [
                    j
                    for j in self.jobs
                    if (j.get("scheduled") or j.get("requires_idle"))
                    and j["action"] != "recover-startup"
                    and j["state"] in ("queued", "running", "deferred")
                ],
            )
            self.persistence_error = None
        except OSError:
            self.persistence_error = (
                "Could not persist maintenance; check disk space and permissions"
            )
            self.emit(self.persistence_error)

    def set_run_intent(self, running):
        revision = self.run_intent.set(running)
        self.want_running = running
        if not running:
            self.disable_recovery()
        return revision

    def disable_recovery(self):
        self.want_running = False
        for job in self.jobs:
            if (job.get("scheduled") or job.get("requires_idle")) and job["state"] in (
                "queued",
                "deferred",
            ):
                job["state"] = "cancelled"
        self.persist_pending()

    def recovery_failed(self, startup=True):
        self.startup_failures += 1
        if self.startup_failures >= MAX_RECOVERY_FAILURES:
            self.recovery_error = "Recovery suspended after five consecutive failures; check the logs and use Start to retry"
            self.disable_recovery()
            self.emit(self.recovery_error)
            return
        delay = (
            (15, 30, 60)[self.startup_failures - 1]
            if startup and self.startup_failures <= 3
            else 300
        )
        self.next_recovery = time.monotonic() + delay
        self.emit(f"Automatic recovery: next startup attempt in {delay}s")

    async def boot(self):
        import fcntl

        self.lock_file = (self.data / "config/supervisor.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Another supervisor already uses this volume")
        await asyncio.to_thread(preflight, self.data)
        self.host_update_task = asyncio.create_task(self.host_updates.run())
        self.worker_task = asyncio.create_task(self.worker())
        self.scheduler_task = asyncio.create_task(self.scheduler())
        self.image_version_task = asyncio.create_task(self.image_version.run())
        self.alert_task = asyncio.create_task(self.alerts.run(self))
        self.cloud_task = asyncio.create_task(self.cloud.run())
        self.access_task = asyncio.create_task(self.file_access.run())
        self.persist_pending()
        if self.run_intent.running:
            self.schedule_once("start")
        else:
            self.emit("Preference preserved: game remains stopped; panel available")

    async def run_steam(self, args, timeout=1800):
        await asyncio.to_thread(install_steam, self.data)
        steam = self.data / "steamcmd"
        command = [
            "FEXInterpreter",
            "/usr/bin/env",
            "LD_LIBRARY_PATH=" + str(steam / "linux32"),
            str(steam / "linux32/steamcmd"),
            "+@sSteamCmdForcePlatformType",
            "linux",
            *args,
        ]
        lines = []

        async def run():
            # Valve's steamcmd.sh re-executes on 42 after a self-update. We run
            # the binary through FEX, so handle that restart here, with a limit.
            for attempt in range(4):
                first_line = len(lines)
                child = await asyncio.create_subprocess_exec(
                    *command,
                    cwd=steam,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                    start_new_session=True,
                )
                try:
                    while line := await child.stdout.readline():
                        text = line.decode(errors="replace").strip()
                        lines.append(text)
                        if len(lines) > 50000:
                            raise RuntimeError("SteamCMD output too large")
                        self.emit("SteamCMD: " + text)
                    code = await child.wait()
                except BaseException:
                    if child.returncode is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        await child.wait()
                    raise
                if code == 42 and attempt < 3:
                    self.emit(
                        "SteamCMD requested a restart after self-update (exit code 42); running again"
                    )
                    continue
                if code:
                    raise SteamError(code, "\n".join(lines[first_line:]))
                return "\n".join(lines)

        # One deadline covers the download and all self-update restarts.
        return await asyncio.wait_for(run(), timeout)

    async def available_build(self):
        output = await self.run_steam(
            ["+login", "anonymous", "+app_info_update", "1", "+app_info_print", "896660", "+quit"],
            timeout=180,
        )
        return public_build(output)

    async def fetch_game(self, stage):
        # Finish SteamCMD's bootstrap before asking it to install the game.
        await self.run_steam(["+quit"], timeout=180)

        async def fetch():
            for attempt in range(3):
                try:
                    output = await self.run_steam(
                        [
                            "+force_install_dir",
                            str(stage),
                            "+login",
                            "anonymous",
                            "+app_info_update",
                            "1",
                            "+app_update",
                            "896660",
                            "validate",
                            "+quit",
                        ]
                    )
                    if "Failed to install app '896660' (Missing configuration)" not in output:
                        return output
                    raise SteamError(0, output)
                except SteamError as exc:
                    missing = "Failed to install app '896660' (Missing configuration)" in exc.output
                    if not missing or exc.code not in (0, 8) or attempt == 2:
                        raise
                    # Invalidate only disposable app metadata, never saves,
                    # downloaded depots, credentials or the current install.
                    for cache in (
                        self.data / "steamcmd/appcache/appinfo.vdf",
                        self.data / "home/Steam/appcache/appinfo.vdf",
                    ):
                        cache.unlink(missing_ok=True)
                    delay = 10 * (attempt + 1)
                    self.emit(
                        f"SteamCMD: Missing configuration; querying metadata again in {delay}s (attempt {attempt + 2}/3)"
                    )
                    await asyncio.sleep(delay)

        return await asyncio.wait_for(fetch(), timeout=1800)

    async def install(self):
        self.state = "installing"
        stage = self.data / "installs" / uuid.uuid4().hex
        stage.mkdir()
        try:
            output = await self.fetch_game(stage)
            if (
                "Success! App '896660' fully installed" not in output
                or not (stage / "valheim_server.x86_64").is_file()
            ):
                raise RuntimeError("SteamCMD did not confirm a valid installation")
            # All game processes are stopped before this method is called.
            pointer = self.data / ".next-current"
            pointer.unlink(missing_ok=True)
            pointer.symlink_to(stage, target_is_directory=True)
            pointer.replace(self.data / "current")
            # Keep current + immediately previous install, not an unbounded history.
            installs = sorted(
                (self.data / "installs").iterdir(), key=lambda p: p.stat().st_mtime, reverse=True
            )
            for old in installs[2:]:
                if old.is_dir() and not old.is_symlink():
                    shutil.rmtree(old)
        except BaseException:
            if self.current_install() != stage:
                shutil.rmtree(stage, ignore_errors=True)
            self.state = "stopped"
            raise

    async def start(self):
        if self.live:
            return
        self.startup_watchdog.reset(None)
        self.join_code = None
        self.game_ready = self.world_loaded = self.transport_ready = False
        self.ready_since = None
        if self.current_install() is None:
            await self.install()
        settings = self.config.effective()
        install = self.current_install()
        if settings.crossplay:
            try:
                await asyncio.to_thread(check_crossplay, install)
            except Exception:
                self.state = "failed"
                raise
        await asyncio.to_thread(install_bepinex, self.data, install, settings)
        steam_lib = self.data / "home/.steam/sdk64"
        steam_lib.mkdir(parents=True, exist_ok=True)
        source = install / "linux64/steamclient.so"
        target = steam_lib / "steamclient.so"
        if source.exists():
            target.unlink(missing_ok=True)
            target.symlink_to(source)
        guest_env = ["SteamAppId=892970", "LD_LIBRARY_PATH=" + guest_library_path(install)]
        if settings.bepinex:
            guest_env += [
                "LD_PRELOAD=" + str(install / "libdoorstop.so"),
                "DOORSTOP_ENABLE=TRUE",
                "DOORSTOP_ENABLED=1",
                "DOORSTOP_TARGET_ASSEMBLY=" + str(install / "BepInEx/core/BepInEx.Preloader.dll"),
                "DOORSTOP_INVOKE_DLL_PATH=" + str(install / "BepInEx/core/BepInEx.Preloader.dll"),
            ]
        self.players.reset()
        if self.closing or not self.want_running or not self.run_intent.running:
            self.state = "stopped"
            self.emit("Startup cancelled by stop preference; no game process started")
            return
        self.state = "starting"
        self.stop_request = None
        self.running = settings
        await asyncio.to_thread(self.mod_diagnostics.begin, settings.bepinex)
        if self.closing or not self.want_running or not self.run_intent.running:
            self.state = "stopped"
            return
        profile, game_environment = self.startup_diagnostics.environment()
        self.clean_stop = False
        atomic_json(self.clean_marker, {"started": time.time(), "world": settings.world_name})
        self.startup_diagnostics.begin(profile, settings)
        self.emit("Starting game with FEX profile: " + profile)
        if profile == "compatible":
            self.emit(
                "FEX compatibility profile: full code checking; performance impact still requires validation"
            )
        try:
            self.process = await asyncio.create_subprocess_exec(
                "FEXInterpreter",
                "/usr/bin/env",
                *guest_env,
                str(install / "valheim_server.x86_64"),
                *settings.arguments(self.data / "saves"),
                cwd=install,
                env=game_environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
                limit=1024 * 1024,
            )
        except BaseException:
            self.startup_diagnostics.finish(None, self.startup_watchdog, spawn_failed=True)
            raise
        self.reader = asyncio.create_task(self.read_game(self.process))

    async def read_game(self, process):
        if self.process is not None and self.process is not process:
            return
        if self.startup_watchdog.process is not process:
            self.startup_watchdog.reset(process)
        steam_server_opened = False

        def oversized_line():
            if self.process is not None and self.process is not process:
                return
            self.players.reset()
            # A discarded line may have described world loading. Unknown phase
            # cannot authorize forced first-scene cleanup.
            self.startup_watchdog.phase_unknown = True
            self.emit(
                "Oversized game line discarded; player presence invalidated and forced startup cleanup blocked"
            )

        try:
            while line := await read_game_line(process.stdout, oversized_line):
                if self.process is not None and self.process is not process:
                    break
                text = line.decode(errors="replace").strip()
                fatal = self.startup_watchdog.consume(
                    text,
                    ready=self.game_ready,
                    allow_fatal=not self.closing and self.stop_request is None,
                )
                self.startup_diagnostics.observe(text, self.startup_watchdog)
                if fatal:
                    self.state = "failed"
                    self.game_ready = False
                    self.join_code = None
                    self.record_failure("game", self.startup_watchdog.fatal)
                    self.emit(
                        "Fatal signal detected in the game; recovery will be evaluated without creating a backup"
                    )
                self.players.consume(text)
                self.player_directory.consume(text)
                if self.running and self.running.crossplay:
                    match = re.search(
                        r'\bSession ".*" (?:registered )?with join code ([0-9]{6})(?![0-9])', text
                    )
                    if match:
                        self.join_code = match[1]
                        self.transport_ready = True
                elif "Game server connected" in text or "Opened Steam server" in text:
                    self.transport_ready = True
                    if re.search(r"(?:^|:\s)Opened Steam server\s*$", text):
                        steam_server_opened = True
                if save_completed(text):
                    self.last_save = time.monotonic()
                # Some builds omit the final ZNet loading message. Require either
                # live world activity or completed chunks plus an active PlayFab
                # session; scene loading and early backend registration are insufficient.
                world_activity = bool(
                    re.search(r"\bConnections \d+ ZDOS:\d+\s+sent:\d+\s+recv:\d+\s*$", text)
                    or re.search(r"\bGot character ZDOID from .+ : \d+:\d+", text)
                )
                active_session = bool(
                    re.search(
                        r'\bSession ".*" with join code [0-9]{6}(?![0-9]).* is active with \d+ player\(s\)',
                        text,
                    )
                )
                if (
                    world_ready(text)
                    or (self.startup_watchdog.world_started and world_activity)
                    or (
                        self.startup_watchdog.chunks_loaded
                        and self.running
                        and not self.running.crossplay
                        and steam_server_opened
                    )
                    or (
                        self.startup_watchdog.chunks_loaded
                        and self.running
                        and self.running.crossplay
                        and active_session
                    )
                ):
                    self.world_loaded = True
                if (
                    self.world_loaded
                    and self.transport_ready
                    and not self.game_ready
                    and not self.startup_watchdog.fatal
                ):
                    self.game_ready = True
                    self.ready_since = time.monotonic()
                    self.state = "online"
                    self.startup_diagnostics.ready()
                self.emit(text)
                self.mod_diagnostics.consume(self.logs[-1]["message"])
        except Exception as exc:
            if self.process is None or self.process is process:
                self.players.reset()
                self.join_code = None
                self.emit("Log reading failed: " + str(exc))
        finally:
            code = await process.wait()
            if self.process is None or self.process is process:
                self.join_code = None
                self.state = "stopped" if code in (0, -signal.SIGINT) else "failed"
                self.players.reset()
                self.emit(f"Game process exited: {code}")
                expected = self.stop_request is not None and self.stop_request[0] is process
                self.startup_diagnostics.finish(code, self.startup_watchdog, expected)
                if not expected and self.want_running and not self.closing:
                    self.record_failure(
                        "game",
                        (
                            self.startup_watchdog.fatal + f"; process exit: {code}"
                            if self.startup_watchdog.fatal
                            else f"Game process exited unexpectedly: {code}"
                        ),
                    )
                    if (
                        self.ready_since is not None
                        and time.monotonic() - self.ready_since >= STABLE_SESSION_SECONDS
                    ):
                        self.startup_failures = 0
                    self.recovery_failed(startup=not self.game_ready)
                self.game_ready = False

    def suspend_startup_recovery(self, message):
        self.recovery_error = message
        self.state = "failed"
        self.disable_recovery()
        self.record_failure("game", message)
        self.emit(message)

    async def wait_game_exit(self, process, seconds):
        deadline = time.monotonic() + seconds
        while process.returncode is None and time.monotonic() < deadline:
            await asyncio.sleep(0.1)
        return process.returncode is not None

    async def recover_startup(self):
        process = self.process
        watchdog = self.startup_watchdog
        if (
            self.closing
            or not self.want_running
            or not self.live
            or watchdog.process is not process
            or not watchdog.fatal
        ):
            return
        if not watchdog.safe_to_terminate:
            self.suspend_startup_recovery(
                "Fatal signal after world loading or with unknown phase; automatic recovery suspended to preserve saves. Stop through the panel or Docker and check the logs."
            )
            return
        self.clean_stop = False
        try:
            self.stop_request = (process, time.monotonic())
            try:
                os.killpg(process.pid, signal.SIGINT)
            except ProcessLookupError:
                pass
            self.emit(
                f"First-scene recovery: SIGINT sent; waiting up to {STARTUP_FATAL_GRACE}s for exit"
            )
            exited = await self.wait_game_exit(process, STARTUP_FATAL_GRACE)
            if self.process is not process or watchdog.process is not process:
                return
            if not exited:
                # Logs may have advanced while SIGINT was being handled.
                if not watchdog.safe_to_terminate:
                    self.suspend_startup_recovery(
                        "The world started loading during shutdown; forced termination cancelled and recovery suspended."
                    )
                    return
                if self.closing:
                    return
                self.emit(
                    "Fatal process stuck before world loading; killing its process group without a backup or save confirmation"
                )
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if not await self.wait_game_exit(process, STARTUP_KILL_TIMEOUT):
                    self.suspend_startup_recovery(
                        "Fatal process did not exit after SIGKILL; recovery suspended. Check the host over SSH."
                    )
                    return
            if self.reader:
                reader = asyncio.ensure_future(self.reader)
                self.reader = reader
                try:
                    await asyncio.wait_for(asyncio.shield(reader), timeout=LOG_DRAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
            self.stop_request = None
            self.state = "failed"
            self.emit(
                "Fatal startup stopped; existing saves preserved and unclean-exit marker retained"
            )
            if self.want_running and not self.closing:
                self.recovery_failed(startup=True)
        except (OSError, RuntimeError) as exc:
            self.suspend_startup_recovery(
                "Could not stop fatal startup; recovery suspended: " + str(exc)
            )

    async def stop(self, safe=True, stop_intent=False, authorize=None):
        process = self.process
        pending = self.stop_request is not None and self.stop_request[0] is process
        if not self.live and not pending:
            if not self.clean_stop:
                raise RuntimeError(
                    "The game exited without a clean shutdown; start and stop it correctly before maintaining saves"
                )
            return
        if not pending:
            if safe:
                if not self.players.idle():
                    raise MaintenanceDeferred(
                        "Waiting for zero players to be confirmed for two minutes"
                    )
                if not self.running.crossplay:
                    count = await asyncio.to_thread(query_steam, self.running.server_port + 1)
                    self.players.observe_steam(count)
                    if count is None:
                        raise MaintenanceDeferred(
                            f"Steam query unavailable (UDP {self.running.server_port + 1}); shutdown not requested. Check the logs or stop through Docker"
                        )
                    if count != 0:
                        raise MaintenanceDeferred(
                            f"Steam query detected {count} player(s), differing from the log; shutdown not requested"
                        )
            pid = process.pid
            if safe:
                # Automatic maintenance freezes admissions and drains
                # captured connection events before requesting a clean save.
                os.killpg(pid, signal.SIGSTOP)
            try:
                if safe:
                    await asyncio.sleep(0.25)
                    if not self.players.idle():
                        raise MaintenanceDeferred(
                            "Connection detected during the final check; shutdown cancelled"
                        )
                if authorize and not authorize():
                    raise JobCancelled("Account changed before shutdown; request cancelled")
                if stop_intent:
                    self.set_run_intent(False)
                requested = time.monotonic()
                os.killpg(pid, signal.SIGINT)
                self.stop_request = (process, requested)
                self.state = "stopping"
                self.emit(
                    f"SIGINT sent to the game; waiting up to {GAME_STOP_TIMEOUT}s for save and exit"
                )
            finally:
                if safe:
                    try:
                        os.killpg(pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
        else:
            if stop_intent and self.run_intent.running:
                self.set_run_intent(False)
            self.emit("Waiting for the requested shutdown; keeping the original deadline")
        requested = self.stop_request[1]
        deadline = requested + GAME_STOP_TIMEOUT
        next_progress = time.monotonic() + STOP_PROGRESS_INTERVAL
        reader = asyncio.ensure_future(self.reader)
        self.reader = reader
        # A descendant may keep stdout open after the game exits. Observe the
        # process returncode too, then allow a bounded drain of the final log.
        while not reader.done():
            if process.returncode is not None:
                try:
                    await asyncio.wait_for(asyncio.shield(reader), timeout=LOG_DRAIN_TIMEOUT)
                except asyncio.TimeoutError:
                    self.emit("Game exited; closing the log reader that remained open")
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.state = "failed"
                raise RuntimeError(
                    f"Shutdown exceeded {GAME_STOP_TIMEOUT}s; game was not killed, backup/restart cancelled"
                )
            if time.monotonic() >= next_progress:
                self.emit(f"Waiting for game save and exit; up to {remaining:.0f}s remaining")
                next_progress = time.monotonic() + STOP_PROGRESS_INTERVAL
            await asyncio.wait([reader], timeout=min(1, remaining))
        if not reader.cancelled():
            await reader
        if process.returncode not in (0, -signal.SIGINT):
            raise RuntimeError("Abnormal exit; backup/update cancelled")
        if self.last_save < requested:
            raise RuntimeError(
                "Exit without a completed-save confirmation; backup/update cancelled"
            )
        self.clean_stop = True
        self.state = "stopped"
        self.clean_marker.unlink(missing_ok=True)
        self.emit("Game stopped with save confirmation")

    def alert_condition(self, key, active):
        try:
            self.alerts.condition(key, active)
        except Exception:
            self.alerts.error = "Could not record alert state on disk."

    async def snapshot(self, reason):
        if self.live:
            raise RuntimeError("A consistent backup requires the game to be stopped")
        if not self.clean_stop:
            raise RuntimeError("Backup cancelled: the last shutdown was not confirmed clean")
        try:
            name = await asyncio.to_thread(
                self.backups.create,
                reason,
                self.running or self.config.effective(),
                self.installed_build(),
            )
        except Exception:
            self.alert_condition("backup", True)
            raise
        self.alert_condition("backup", False)
        self.cloud.wake.set()
        settings = self.config.effective()
        if reason != "pre-restore":
            await asyncio.to_thread(
                self.backups.prune,
                settings.retain_daily,
                settings.retain_weekly,
                set(self.cloud.pending()),
            )
        return name

    async def execute(self, job):
        action = job["action"]
        if job.get("actor") and self.account_authorizer and not self.account_authorizer(job):
            raise JobCancelled("Account or credentials changed; request cancelled")
        guarded_stop = bool(job.get("requires_idle") and action == "stop")
        if (
            self.host_updates.busy
            and (job.get("scheduled") or job.get("requires_idle"))
            and action in ("stop", "restart", "backup", "update")
        ):
            raise MaintenanceDeferred("Image update in progress; automatic maintenance deferred")
        if job.get("scheduled") and (not self.want_running or not self.run_intent.running):
            job["result"] = "Automatic task skipped: the game should remain stopped"
            return
        if action in ("start", "stop") and not job.get("scheduled") and not guarded_stop:
            revision = job.get("intent_revision")
            if revision is not None and revision != self.run_intent.revision:
                job["result"] = "Request replaced by a newer preference"
                return
            if revision is None:
                self.set_run_intent(action == "start")
        if action == "recover-startup":
            await self.recover_startup()
            if self.recovery_error:
                raise RuntimeError(self.recovery_error)
            job["result"] = (
                "Fatal startup handled; the next attempt respects recovery limits and stop preference"
            )
            return
        if action == "start":
            if job.get("scheduled") and not self.want_running:
                return
            if not job.get("scheduled"):
                self.startup_failures = 0
                self.recovery_error = None
            self.want_running = True
            await self.start()
            return
        if action == "stop" and not self.live:
            if guarded_stop:
                self.set_run_intent(False)
            self.disable_recovery()
            return
        if action == "check-update":
            self.latest_build = await self.available_build()
            if (
                self.installed_build()
                and self.latest_build != self.installed_build()
                and self.latest_build != self.failed_update_build
            ):
                self.schedule_once("update")
            return
        if action == "restore":
            if self.live:
                raise RuntimeError("Stop the game before restoring")
            await asyncio.to_thread(self.backups.validate, self.backups.resolve(job["backup"]))
            job["backup_created"] = await self.snapshot("pre-restore")
            manifest = await asyncio.to_thread(self.backups.restore, job["backup"])
            job["result"] = "Saves restored. Archived world: " + manifest["world"]
            return
        if action == "update" and job.get("scheduled") and self.latest_build is not None:
            if self.installed_build() == self.latest_build:
                job["result"] = "Version already installed; shutdown skipped"
                return
        if (job.get("scheduled") or job.get("requires_idle")) and (
            not self.live or not self.game_ready
        ):
            raise MaintenanceDeferred("Waiting for the game to become ready before maintenance")
        was_running = self.live
        try:
            # Explicit administrator actions bypass admission/idle checks only.
            # Exit and completed-save confirmation remain mandatory.
            if job.get("requires_idle"):
                authorize = (
                    (lambda: self.account_authorizer(job)) if self.account_authorizer else None
                )
                await self.stop(safe=True, stop_intent=guarded_stop, authorize=authorize)
            else:
                await self.stop(safe=bool(job.get("scheduled") or job.get("requires_idle")))
        except MaintenanceDeferred:
            raise
        except Exception:
            # A failed save/stop must never replay the whole maintenance job.
            # If the process has exited, recover the game independently.
            if (
                action == "stop"
                and self.stop_request is not None
                and self.stop_request[0] is self.process
            ):
                # Once a manual stop was actually signalled, keep that intent
                # even if exit/save confirmation fails. Never turn it into a start.
                if guarded_stop and self.run_intent.running:
                    self.set_run_intent(False)
                self.disable_recovery()
            elif was_running and not self.live:
                self.next_recovery = time.monotonic() + 300
            raise
        if action == "stop":
            if guarded_stop and self.run_intent.running:
                self.set_run_intent(False)
            self.disable_recovery()
            return
        try:
            job["result"] = await self.snapshot(action)
            job["backup_created"] = job["result"]
            if action == "update":
                await self.install()
        finally:
            if (
                was_running
                and not self.live
                and not self.closing
                and self.want_running
                and self.run_intent.running
            ):
                try:
                    await self.start()
                except Exception:
                    self.recovery_failed(startup=False)
                    raise
        if not job.get("scheduled"):
            # A completed manual maintenance already satisfied equivalent work.
            for pending in self.jobs:
                if (
                    pending.get("scheduled")
                    and pending["state"] in ("queued", "deferred")
                    and (
                        pending["action"] in ("backup", "restart")
                        or action == pending["action"] == "update"
                    )
                ):
                    pending["state"] = "cancelled"
            self.persist_pending()
        if action == "update":
            self.failed_update_build = None

    async def worker(self):
        while True:
            job = await self.queue.get()
            if job["state"] == "cancelled":
                self.queue.task_done()
                continue
            self.active = job
            job.setdefault("started", time.time())
            job["attempts"] = job.get("attempts", 0) + 1
            attempt_started = time.monotonic()
            job["state"] = "running"
            job.pop("error", None)
            self.persist_history()
            self.emit("Running operation: " + job["action"])
            try:
                if job.get("scheduled") and job["action"] != "recover-startup":
                    self.persist_pending()
                    if self.persistence_error:
                        raise MaintenanceDeferred(self.persistence_error)
                await self.execute(job)
                job["state"] = "completed"
            except JobCancelled as exc:
                job["state"], job["result"] = "cancelled", str(exc)
            except Exception as exc:
                job["state"], job["error"] = "failed", str(exc)
                deferred = (
                    isinstance(exc, MaintenanceDeferred)
                    and (job.get("scheduled") or job.get("requires_idle"))
                    and job["action"] in ("stop", "restart", "backup", "update")
                )
                if not deferred and job["action"] == "backup":
                    self.alert_condition("backup", True)
                if not deferred:
                    self.record_failure(job["action"], str(exc))
                self.emit(
                    ("Operation deferred" if deferred else "Operation failed")
                    + " ("
                    + job["action"]
                    + "): "
                    + str(exc)
                )
                if job["action"] == "update" and not deferred:
                    self.failed_update_build = self.latest_build
                if job["action"] == "start":
                    self.recovery_failed(startup=False)
                if deferred:
                    job["state"] = "deferred"
                    self.next_retry = time.monotonic() + 300
            except asyncio.CancelledError:
                job["state"] = "failed"
                job["error"] = (
                    "Operation interrupted by the supervisor; it will not repeat automatically"
                )
                raise
            finally:
                job["duration"] = job.get("duration", 0) + max(
                    0, time.monotonic() - attempt_started
                )
                job["last_attempt_finished"] = time.time()
                self.active = None
                self.persist_pending()
                self.queue.task_done()

    def schedule_once(self, action):
        if action in ("restart", "backup", "update"):
            for pending in self.jobs:
                if (
                    pending.get("scheduled")
                    and pending["state"] in ("queued", "deferred")
                    and pending["action"] in ("restart", "backup", "update")
                ):
                    priorities = {"backup": 0, "restart": 1, "update": 2}
                    pending["action"] = max((action, pending["action"]), key=priorities.get)
                    self.persist_pending()
                    return
        if any(
            j["action"] == action and j["state"] in ("queued", "running", "deferred")
            for j in self.jobs
        ):
            return
        job = {
            "id": uuid.uuid4().hex,
            "action": action,
            "state": "queued",
            "scheduled": True,
            "created": time.time(),
        }
        self.queue.put_nowait(job)
        self.jobs.append(job)
        self.trim_jobs()
        self.persist_pending()

    async def scheduler(self):
        while True:
            try:
                await self.tick()
            except Exception as exc:
                self.emit("Agendador: " + str(exc))
            await asyncio.sleep(5)

    async def tick(self):
        self.audit.image_result(self.host_updates.current)
        if time.monotonic() >= self.next_history_prune:
            self.persist_history()
            self.next_history_prune = time.monotonic() + 3600
        if self.active is not None or self.closing:
            return
        if (
            self.live
            and self.want_running
            and self.run_intent.running
            and self.startup_watchdog.fatal
        ):
            if self.queue.empty():
                self.schedule_once("recover-startup")
            return
        if (
            not self.live
            and self.want_running
            and self.run_intent.running
            and self.queue.empty()
            and time.monotonic() >= self.next_recovery
        ):
            self.next_recovery = time.monotonic() + 300
            self.emit("Automatically attempting to start the server")
            self.schedule_once("start")
        # Image verification must allow startup/crash recovery, while daily
        # maintenance remains paused so it cannot interrupt the new game.
        if self.host_updates.busy:
            return
        settings = self.config.effective()
        now = datetime.now(ZoneInfo(settings.timezone))
        if self.live and self.game_ready and self.want_running and self.run_intent.running:
            # Query first: a newly found update can absorb the queued daily
            # restart/backup before that maintenance operation starts.
            update_at = settings.restart_at if settings.restart_at != "off" else settings.backup_at
            if update_at == "off":
                update_at = "05:00"
            due = []
            for action, hour in (
                ("check-update", update_at if settings.auto_update else "off"),
                ("restart", settings.restart_at),
                ("backup", settings.backup_at),
            ):
                key = action + ":" + now.date().isoformat()
                if hour != "off" and now.strftime("%H:%M") >= hour and key not in self.daily:
                    scheduled_at = now.replace(
                        hour=int(hour[:2]), minute=int(hour[3:]), second=0, microsecond=0
                    )
                    if scheduled_at > self.schedule_started:
                        due.append((action, key))
            if due:
                # Check first so a new update absorbs the same daily stop.
                if any(action == "check-update" for action, _ in due):
                    self.schedule_once("check-update")
                maintenance = {action for action, _ in due} - {"check-update"}
                if maintenance:
                    self.schedule_once("restart" if "restart" in maintenance else "backup")
                self.daily.update({key: True for _, key in due})
                self.daily = {
                    k: v
                    for k, v in self.daily.items()
                    if k.split(":", 1)[1] >= now.date().isoformat()
                }
                atomic_json(self.daily_path, self.daily)
        if (
            self.want_running
            and self.live
            and self.game_ready
            and time.monotonic() >= self.next_retry
        ):
            for job in self.jobs:
                if job["state"] == "deferred":
                    job["state"] = "queued"
                    self.queue.put_nowait(job)

    async def shutdown(self):
        self.closing = True
        self.want_running = False
        self.emit("Container shutdown requested; suspending tasks and preparing game shutdown")
        if self.host_update_task:
            self.host_update_task.cancel()
            await asyncio.gather(self.host_update_task, return_exceptions=True)
        if self.image_version_task:
            self.image_version_task.cancel()
            await asyncio.gather(self.image_version_task, return_exceptions=True)
        if self.alert_task:
            self.alert_task.cancel()
            await asyncio.gather(self.alert_task, return_exceptions=True)
        if self.access_task:
            self.access_task.cancel()
            await asyncio.gather(self.access_task, return_exceptions=True)
        if self.cloud_task:
            self.cloud_task.cancel()
            await asyncio.gather(self.cloud_task, return_exceptions=True)
        if self.scheduler_task:
            self.scheduler_task.cancel()
            await asyncio.gather(self.scheduler_task, return_exceptions=True)
        # Finish any operation that is already touching saves or installs.
        if self.worker_task:
            # Discard queued operations, preserving scheduled intents on disk.
            while not self.queue.empty():
                queued = self.queue.get_nowait()
                if queued["state"] != "cancelled":
                    queued["state"] = "deferred" if queued.get("scheduled") else "cancelled"
                self.queue.task_done()
        if self.active and self.active["action"] != "check-update":
            self.emit(
                f"Waiting up to {SHUTDOWN_JOB_TIMEOUT}s for active operation {self.active['action']}"
            )
            try:
                await asyncio.wait_for(self.queue.join(), timeout=SHUTDOWN_JOB_TIMEOUT)
            except asyncio.TimeoutError:
                self.emit("Supervisor was waiting for an operation when shutdown arrived")
        if self.worker_task:
            self.worker_task.cancel()
            await asyncio.gather(self.worker_task, return_exceptions=True)
        self.persist_pending()
        # Explicit container shutdown is an administrator action, not an automatic
        # maintenance decision; it must honor Docker's termination deadline.
        try:
            if self.live or self.stop_request is not None:
                await self.stop(safe=False)
        finally:
            if self.lock_file:
                self.lock_file.close()
        self.emit("Supervisor stopped")
