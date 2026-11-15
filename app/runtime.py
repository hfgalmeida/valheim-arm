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
from .image_version import ImageVersion
from .mod_diagnostics import ModDiagnostics
from .alerts import Alerts
from .backups import Backups, digest
from .cloud import Cloud
from .file_access import FileAccess
from .config import ConfigStore, atomic_json
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


class MaintenanceDeferred(RuntimeError):
    """Safety checks refused maintenance before any stop signal was sent."""


class SteamError(RuntimeError):
    def __init__(self, code, output):
        self.code, self.output = code, output
        super().__init__(
            f"SteamCMD falhou (código de saída {code}); consulte os logs e /data/home/Steam/logs/stderr.txt"
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
        raise RuntimeError("Metadados do app não encontrados")
    tokens = iter(re.findall(r'"([^"\n]*)"|([{}])', output[match.end() :]))

    def block():
        result = {}
        for string, brace in tokens:
            if brace == "}":
                return result
            if brace:
                raise ValueError("Metadados Steam inválidos")
            key = string
            value, mark = next(tokens)
            result[key] = block() if mark == "{" else value
        raise ValueError("Metadados Steam incompletos")

    try:
        value = block()["depots"]["branches"]["public"]["buildid"]
        if not value.isdigit():
            raise ValueError("Build inválido")
        return value
    except (KeyError, StopIteration, ValueError) as exc:
        raise RuntimeError("Build público não encontrado; atualização adiada") from exc


def preflight(data: Path):
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise RuntimeError("Execute a imagem em um host Linux ARM64 na instância Oracle")
    kernel = tuple(map(int, platform.release().split("-")[0].split(".")[:2]))
    if kernel < (5, 15) or os.sysconf("SC_PAGE_SIZE") != 4096:
        raise RuntimeError("A plataforma de referência exige kernel >=5.15 e páginas de 4 KiB")
    features = Path("/proc/cpuinfo").read_text()
    if not all(feature in features for feature in ("atomics", "crc32", "asimd", "dcpop")):
        raise RuntimeError("CPU ARMv8.2 com atomics/CRC/ASIMD necessária para esta imagem")
    if not shutil.which("FEXInterpreter") or not Path("/opt/rootfs/usr/bin/env").exists():
        raise RuntimeError("FEX ou rootfs ausente; reconstrua a imagem")
    data.mkdir(parents=True, exist_ok=True)
    (data / "home").mkdir(exist_ok=True)
    (data / "cache").mkdir(exist_ok=True)
    with tempfile.TemporaryFile(dir=data):
        pass
    if shutil.disk_usage(data).free < 6 * 1024**3:
        raise RuntimeError("Disponibilize ao menos 6 GiB livres para instalação e atualização")
    result = subprocess.run(
        ["FEXInterpreter", "/usr/bin/env", "VALHEIM_PREFLIGHT=1", "/usr/bin/uname", "-m"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode or "x86_64" not in result.stdout:
        raise RuntimeError(
            "Smoke test do FEX falhou; confira kernel, rootfs e logs: " + result.stderr[-1000:]
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
        raise RuntimeError("Crossplay: biblioteca Party não encontrada na instalação do Valheim")
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
                "Crossplay: dependência x86 ausente ou incompatível; atualize a imagem Docker. "
                + result.stderr[-2000:]
            )


def download(url, target, limit=256 * 1024**2):
    if not url.startswith("https://"):
        raise ValueError("Download exige HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "valheim-arm"})
    with urllib.request.urlopen(request, timeout=60) as source, target.open("wb") as output:
        if not source.url.startswith("https://"):
            raise ValueError("Redirecionamento para protocolo inseguro")
        total = 0
        while chunk := source.read(1024 * 1024):
            total += len(chunk)
            if total > limit:
                raise ValueError("Download excede o limite")
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
        raise RuntimeError("Instalação do SteamCMD incompleta")


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
            raise ValueError(
                "Links simbólicos não são permitidos nos destinos gerenciados do BepInEx"
            )
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
                raise ValueError("SHA256 do BepInEx não confere")
            unpack = Path(tmp) / "unpack"
            unpack.mkdir()
            with zipfile.ZipFile(archive) as pack:
                if sum(p.file_size for p in pack.infolist()) > 1024**3:
                    raise ValueError("Pacote BepInEx excessivo")
                for item in pack.infolist():
                    parts = Path(item.filename).parts
                    if (
                        ".." in parts
                        or item.filename.startswith(("/", "\\"))
                        or "\\" in item.filename
                        or ":" in item.filename
                    ):
                        raise ValueError("Caminho inseguro no pacote BepInEx")
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Links não permitidos no pacote BepInEx")
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
                raise ValueError("Use BepInExPack_Valheim Linux com Doorstop x64 e BepInEx/core")
            source, library = roots[0]
            if not all(
                (source / "BepInEx/core" / name).is_file()
                for name in ("BepInEx.dll", "BepInEx.Preloader.dll")
            ):
                raise ValueError("Pacote BepInEx incompleto")
            for directory in ("plugins", "config", "patchers"):
                (mods / "BepInEx" / directory).mkdir(parents=True, exist_ok=True)
            core = mods / "BepInEx/core"
            if core.is_symlink():
                raise ValueError("BepInEx/core não pode ser um link simbólico")
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
            raise ValueError("Instalação contém BepInEx fora do diretório gerenciado")
        link.symlink_to(destination, target_is_directory=name == "BepInEx")


class Runtime:
    def __init__(self, data: Path, config=None):
        self.data = data
        for directory in ("config", "saves", "home", "cache", "installs", "mods"):
            (data / directory).mkdir(parents=True, exist_ok=True)
        self.config = config or ConfigStore(data)
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
        self.state = "parado"
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
            self.jobs.append(job)
            if job["state"] == "executando":
                job["state"] = "falha"
                job["error"] = (
                    "Manutenção interrompida pelo supervisor; não será repetida automaticamente"
                )
                self.emit(job["error"])
            elif job["state"] in ("fila", "adiado"):
                if not self.run_intent.running:
                    job["state"] = "cancelado"
                    continue
                job["state"] = "fila"
                self.queue.put_nowait(job)

        pending_ids = {job["id"] for job in self.jobs}
        for job in self.maintenance_history.load():
            if job["id"] in pending_ids:
                continue
            if job["state"] in ("fila", "adiado", "executando"):
                job["state"] = "falha" if job["state"] == "executando" else "cancelado"
                job["error"] = (
                    "Operação interrompida pelo supervisor; não será repetida automaticamente"
                )
                job["finished"] = time.time()
            self.jobs.append(job)
        self.jobs = deque(sorted(self.jobs, key=lambda job: job.get("created", 0)))
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
                message = message.replace(password, "[senha]")
        self.logs.append({"time": time.time(), "message": message[:4000]})
        log.info("%s", message[:4000])

    def record_failure(self, source, message):
        for password in {
            self.config.effective().server_password,
            self.running.server_password if self.running else "",
        }:
            if password:
                message = message.replace(password, "[senha]")
        self.last_failure = {"time": time.time(), "source": source, "message": message[:1000]}

    def diagnostics(self):
        retry = None
        if self.closing:
            phase, detail = "stopping", "Supervisor encerrando."
        elif self.state == "parando":
            phase, detail = "stopping", "Aguardando salvamento e saída do jogo."
        elif self.live and self.startup_watchdog.fatal:
            phase = (
                "recovering"
                if self.active and self.active["action"] == "recover-startup"
                else "failed"
            )
            detail = (
                self.recovery_error
                or "Sinal fatal detectado; aguardando recuperação controlada da inicialização."
            )
        elif self.live:
            if self.game_ready:
                phase, detail = "ready", "Mundo carregado e conexão do servidor inicializada."
            elif not self.world_loaded:
                phase, detail = "starting", "Aguardando o carregamento do mundo."
            else:
                phase, detail = "starting", "Mundo carregado; aguardando a conexão do servidor."
        elif self.active and self.active["action"] in ("start", "restart", "update", "restore"):
            phase, detail = "starting", "Preparando o jogo: " + self.state + "."
        elif self.run_intent.error:
            phase, detail = "failed", self.run_intent.error
        elif self.recovery_error:
            phase, detail = "failed", self.recovery_error
        elif self.want_running and self.next_recovery > 0:
            phase, detail = "recovering", "Nova tentativa automática de inicialização agendada."
            retry = max(0, int(self.next_recovery - time.monotonic()))
        elif self.state == "falha":
            phase, detail = (
                "failed",
                "O jogo encontrou uma falha; consulte a última falha e os logs.",
            )
        else:
            phase, detail = "stopped", "Jogo parado."
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
            "mods": self.mod_diagnostics.describe(self.live, self.config.effective().bepinex),
            "state": self.state,
            "running": self.live,
            "players": (
                self.players.status() if self.live else {"count": 0, "reason": "Servidor parado"}
            ),
            "crossplay": crossplay,
            "join_code": self.join_code if self.live and crossplay else None,
            "pending_config": self.running is not None and self.running != self.config.effective(),
            "installed_build": self.installed_build(),
            "available_build": self.latest_build,
            "maintenance_persistence_error": self.persistence_error,
            "maintenance_history_error": self.maintenance_history.error,
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

    def submit(self, action, backup=None):
        if action not in ("start", "stop", "restart", "update", "backup", "restore"):
            raise ValueError("Ação desconhecida")
        if action == "restore":
            self.backups.resolve(backup or "")
        for pending in self.jobs:
            if (
                not pending.get("scheduled")
                and pending["action"] == action
                and pending.get("backup") == backup
                and pending["state"] in ("fila", "executando")
                and (
                    action not in ("start", "stop")
                    or pending.get("intent_revision") == self.run_intent.revision
                )
            ):
                return pending
        if self.queue.full():
            raise asyncio.QueueFull
        revision = None
        if action in ("start", "stop"):
            revision = self.set_run_intent(action == "start")
            for pending in self.jobs:
                if pending["action"] in ("start", "stop") and pending["state"] == "fila":
                    pending["state"] = "cancelado"
        job = {
            "id": uuid.uuid4().hex,
            "action": action,
            "backup": backup,
            "state": "fila",
            "created": time.time(),
        }
        if revision is not None:
            job["intent_revision"] = revision
        self.queue.put_nowait(job)
        self.jobs.append(job)
        self.persist_history()
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
            if job["state"] not in ("fila", "executando", "adiado"):
                job.setdefault("finished", now)
            for field in ("error", "result"):
                if isinstance(job.get(field), str):
                    for secret in secrets:
                        if secret:
                            job[field] = job[field].replace(secret, "[senha]")
                    job[field] = job[field][:1000]
        self.trim_jobs()
        self.maintenance_history.save(self.jobs, secrets)

    def persist_pending(self):
        self.persist_history()
        try:
            atomic_json(
                self.pending_path,
                [
                    j
                    for j in self.jobs
                    if j.get("scheduled")
                    and j["action"] != "recover-startup"
                    and j["state"] in ("fila", "executando", "adiado")
                ],
            )
            self.persistence_error = None
        except OSError:
            self.persistence_error = (
                "Não foi possível persistir manutenção; confira espaço e permissões"
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
            if job.get("scheduled") and job["state"] in ("fila", "adiado"):
                job["state"] = "cancelado"
        self.persist_pending()

    def recovery_failed(self, startup=True):
        self.startup_failures += 1
        if self.startup_failures >= MAX_RECOVERY_FAILURES:
            self.recovery_error = "Recuperação suspensa após cinco falhas consecutivas; consulte os logs e use Iniciar para tentar novamente"
            self.disable_recovery()
            self.emit(self.recovery_error)
            return
        delay = (
            (15, 30, 60)[self.startup_failures - 1]
            if startup and self.startup_failures <= 3
            else 300
        )
        self.next_recovery = time.monotonic() + delay
        self.emit(f"Recuperação automática: nova tentativa de iniciar em {delay}s")

    async def boot(self):
        import fcntl

        self.lock_file = (self.data / "config/supervisor.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Outro supervisor já utiliza este volume")
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
            self.emit("Preferência preservada: jogo permanece parado; painel disponível")

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
                            raise RuntimeError("Saída SteamCMD excessiva")
                        self.emit("SteamCMD: " + text)
                    code = await child.wait()
                except BaseException:
                    if child.returncode is None:
                        os.killpg(child.pid, signal.SIGKILL)
                        await child.wait()
                    raise
                if code == 42 and attempt < 3:
                    self.emit(
                        "SteamCMD solicitou reinício após autoatualização (código 42); executando novamente"
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
                        f"SteamCMD: Missing configuration; metadados serão consultados novamente em {delay}s (tentativa {attempt + 2}/3)"
                    )
                    await asyncio.sleep(delay)

        return await asyncio.wait_for(fetch(), timeout=1800)

    async def install(self):
        self.state = "instalando"
        stage = self.data / "installs" / uuid.uuid4().hex
        stage.mkdir()
        try:
            output = await self.fetch_game(stage)
            if (
                "Success! App '896660' fully installed" not in output
                or not (stage / "valheim_server.x86_64").is_file()
            ):
                raise RuntimeError("SteamCMD não confirmou instalação válida")
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
            self.state = "parado"
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
                self.state = "falha"
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
            self.state = "parado"
            self.emit(
                "Inicialização cancelada pela intenção de parada; nenhum processo do jogo iniciado"
            )
            return
        self.state = "iniciando"
        self.stop_request = None
        self.running = settings
        await asyncio.to_thread(self.mod_diagnostics.begin, settings.bepinex)
        if self.closing or not self.want_running or not self.run_intent.running:
            self.state = "parado"
            return
        self.clean_stop = False
        atomic_json(self.clean_marker, {"started": time.time(), "world": settings.world_name})
        self.process = await asyncio.create_subprocess_exec(
            "FEXInterpreter",
            "/usr/bin/env",
            *guest_env,
            str(install / "valheim_server.x86_64"),
            *settings.arguments(self.data / "saves"),
            cwd=install,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            limit=1024 * 1024,
        )
        self.reader = asyncio.create_task(self.read_game(self.process))

    async def read_game(self, process):
        if self.process is not None and self.process is not process:
            return
        if self.startup_watchdog.process is not process:
            self.startup_watchdog.reset(process)
        try:
            while line := await process.stdout.readline():
                if self.process is not None and self.process is not process:
                    break
                text = line.decode(errors="replace").strip()
                fatal = self.startup_watchdog.consume(
                    text,
                    ready=self.game_ready,
                    allow_fatal=not self.closing and self.stop_request is None,
                )
                if fatal:
                    self.state = "falha"
                    self.game_ready = False
                    self.join_code = None
                    self.record_failure("game", self.startup_watchdog.fatal)
                    self.emit(
                        "Sinal fatal detectado no jogo; recuperação será avaliada sem criar backup"
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
                self.emit(text)
                self.mod_diagnostics.consume(self.logs[-1]["message"])
        except Exception as exc:
            if self.process is None or self.process is process:
                self.players.reset()
                self.join_code = None
                self.emit("Leitura do log falhou: " + str(exc))
        finally:
            code = await process.wait()
            if self.process is None or self.process is process:
                self.join_code = None
                self.state = "parado" if code in (0, -signal.SIGINT) else "falha"
                self.players.reset()
                self.emit(f"Processo do jogo encerrado: {code}")
                expected = self.stop_request is not None and self.stop_request[0] is process
                if not expected and self.want_running and not self.closing:
                    self.record_failure(
                        "game", f"Processo do jogo encerrado inesperadamente: {code}"
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
        self.state = "falha"
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
                "Sinal fatal com mundo iniciado ou fase desconhecida; recuperação automática suspensa para preservar os saves. Solicite parada pelo painel ou Docker e confira os logs."
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
                f"Recuperação da primeira cena: SIGINT enviado; aguardando saída por até {STARTUP_FATAL_GRACE}s"
            )
            exited = await self.wait_game_exit(process, STARTUP_FATAL_GRACE)
            if self.process is not process or watchdog.process is not process:
                return
            if not exited:
                # Logs may have advanced while SIGINT was being handled.
                if not watchdog.safe_to_terminate:
                    self.suspend_startup_recovery(
                        "O mundo começou a carregar durante a parada; encerramento forçado cancelado e recuperação suspensa."
                    )
                    return
                if self.closing:
                    return
                self.emit(
                    "Processo fatal travado antes de carregar o mundo; encerrando grupo com SIGKILL, sem backup nem confirmação de salvamento"
                )
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                if not await self.wait_game_exit(process, STARTUP_KILL_TIMEOUT):
                    self.suspend_startup_recovery(
                        "Processo fatal não encerrou após SIGKILL; recuperação suspensa. Verifique a instância pelo SSH."
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
            self.state = "falha"
            self.emit(
                "Inicialização fatal encerrada; saves existentes preservados e marcador de saída incompleta mantido"
            )
            if self.want_running and not self.closing:
                self.recovery_failed(startup=True)
        except (OSError, RuntimeError) as exc:
            self.suspend_startup_recovery(
                "Falha ao encerrar inicialização fatal; recuperação suspensa: " + str(exc)
            )

    async def stop(self, safe=True):
        process = self.process
        pending = self.stop_request is not None and self.stop_request[0] is process
        if not self.live and not pending:
            if not self.clean_stop:
                raise RuntimeError(
                    "O jogo terminou sem parada limpa; inicie e encerre corretamente antes de manter os saves"
                )
            return
        if not pending:
            if safe:
                if not self.players.idle():
                    raise MaintenanceDeferred(
                        "Aguardando zero jogadores confirmado por dois minutos"
                    )
                if not self.running.crossplay:
                    count = await asyncio.to_thread(query_steam, self.running.server_port + 1)
                    if count is None:
                        raise MaintenanceDeferred(
                            f"Consulta Steam indisponível (UDP {self.running.server_port + 1}); parada não solicitada ao jogo. Confira os logs ou solicite a parada pelo Docker"
                        )
                    if count != 0:
                        raise MaintenanceDeferred(
                            f"Consulta Steam detectou {count} jogador(es), em divergência com o log; parada não solicitada ao jogo"
                        )
            pid = process.pid
            if safe:
                # Scheduled/panel operations still freeze admissions and drain
                # captured connection events before requesting a clean save.
                os.killpg(pid, signal.SIGSTOP)
            try:
                if safe:
                    await asyncio.sleep(0.25)
                    if not self.players.idle():
                        raise MaintenanceDeferred(
                            "Conexão detectada na verificação final; parada cancelada"
                        )
                requested = time.monotonic()
                os.killpg(pid, signal.SIGINT)
                self.stop_request = (process, requested)
                self.state = "parando"
                self.emit(
                    f"SIGINT enviado ao jogo; aguardando salvamento e saída por até {GAME_STOP_TIMEOUT}s"
                )
            finally:
                if safe:
                    try:
                        os.killpg(pid, signal.SIGCONT)
                    except ProcessLookupError:
                        pass
        else:
            self.emit("Aguardando a parada já solicitada; mantendo o prazo original")
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
                    self.emit("Jogo encerrado; finalizando leitura do log que permaneceu aberta")
                    reader.cancel()
                    await asyncio.gather(reader, return_exceptions=True)
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self.state = "falha"
                raise RuntimeError(
                    f"Parada excedeu {GAME_STOP_TIMEOUT}s; jogo não foi morto, backup/reinício cancelados"
                )
            if time.monotonic() >= next_progress:
                self.emit(f"Aguardando o jogo salvar e encerrar; restam até {remaining:.0f}s")
                next_progress = time.monotonic() + STOP_PROGRESS_INTERVAL
            await asyncio.wait([reader], timeout=min(1, remaining))
        if not reader.cancelled():
            await reader
        if process.returncode not in (0, -signal.SIGINT):
            raise RuntimeError("Saída anormal; backup/atualização cancelados")
        if self.last_save < requested:
            raise RuntimeError(
                "Saída sem confirmação de salvamento concluído; backup/atualização cancelados"
            )
        self.clean_stop = True
        self.state = "parado"
        self.clean_marker.unlink(missing_ok=True)
        self.emit("Jogo encerrado com salvamento confirmado")

    def alert_condition(self, key, active):
        try:
            self.alerts.condition(key, active)
        except Exception:
            self.alerts.error = "Falha ao registrar estado de alerta no disco."

    async def snapshot(self, reason):
        if self.live:
            raise RuntimeError("Backup consistente exige servidor parado")
        if not self.clean_stop:
            raise RuntimeError(
                "Backup cancelado: último encerramento não foi confirmado como limpo"
            )
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
        if job.get("scheduled") and (not self.want_running or not self.run_intent.running):
            job["result"] = "Tarefa automática dispensada: servidor deve permanecer parado"
            return
        if action in ("start", "stop") and not job.get("scheduled"):
            revision = job.get("intent_revision")
            if revision is not None and revision != self.run_intent.revision:
                job["result"] = "Solicitação substituída por uma preferência mais recente"
                return
            if revision is None:
                self.set_run_intent(action == "start")
        if action == "recover-startup":
            await self.recover_startup()
            if self.recovery_error:
                raise RuntimeError(self.recovery_error)
            job["result"] = (
                "Inicialização fatal tratada; nova tentativa respeita o limite de recuperação e a intenção de parada"
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
                raise RuntimeError("Pare o jogo antes de restaurar")
            await asyncio.to_thread(self.backups.validate, self.backups.resolve(job["backup"]))
            job["backup_created"] = await self.snapshot("pre-restore")
            manifest = await asyncio.to_thread(self.backups.restore, job["backup"])
            job["result"] = "Saves restaurados. Mundo do arquivo: " + manifest["world"]
            return
        if action == "update" and job.get("scheduled") and self.latest_build is not None:
            if self.installed_build() == self.latest_build:
                job["result"] = "Versão já instalada; parada dispensada"
                return
        if job.get("scheduled") and (not self.live or not self.game_ready):
            raise MaintenanceDeferred("Aguardando o jogo ficar pronto antes da manutenção")
        was_running = self.live
        try:
            # Explicit administrator actions bypass admission/idle checks only.
            # Exit and completed-save confirmation remain mandatory.
            await self.stop(safe=bool(job.get("scheduled")))
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
                self.disable_recovery()
            elif was_running and not self.live:
                self.next_recovery = time.monotonic() + 300
            raise
        if action == "stop":
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
                    and pending["state"] in ("fila", "adiado")
                    and (
                        pending["action"] in ("backup", "restart")
                        or action == pending["action"] == "update"
                    )
                ):
                    pending["state"] = "cancelado"
            self.persist_pending()
        if action == "update":
            self.failed_update_build = None

    async def worker(self):
        while True:
            job = await self.queue.get()
            if job["state"] == "cancelado":
                self.queue.task_done()
                continue
            self.active = job
            job.setdefault("started", time.time())
            job["attempts"] = job.get("attempts", 0) + 1
            attempt_started = time.monotonic()
            job["state"] = "executando"
            job.pop("error", None)
            self.persist_history()
            self.emit("Executando operação: " + job["action"])
            try:
                if job.get("scheduled") and job["action"] != "recover-startup":
                    self.persist_pending()
                    if self.persistence_error:
                        raise MaintenanceDeferred(self.persistence_error)
                await self.execute(job)
                job["state"] = "concluído"
            except Exception as exc:
                job["state"], job["error"] = "falha", str(exc)
                deferred = (
                    isinstance(exc, MaintenanceDeferred)
                    and job.get("scheduled")
                    and job["action"] in ("restart", "backup", "update")
                )
                if not deferred and job["action"] == "backup":
                    self.alert_condition("backup", True)
                if not deferred:
                    self.record_failure(job["action"], str(exc))
                self.emit(
                    ("Operação adiada" if deferred else "Operação falhou")
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
                    job["state"] = "adiado"
                    self.next_retry = time.monotonic() + 300
            except asyncio.CancelledError:
                job["state"] = "falha"
                job["error"] = (
                    "Operação interrompida pelo supervisor; não será repetida automaticamente"
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
                    and pending["state"] in ("fila", "adiado")
                    and pending["action"] in ("restart", "backup", "update")
                ):
                    priorities = {"backup": 0, "restart": 1, "update": 2}
                    pending["action"] = max((action, pending["action"]), key=priorities.get)
                    self.persist_pending()
                    return
        if any(
            j["action"] == action and j["state"] in ("fila", "executando", "adiado")
            for j in self.jobs
        ):
            return
        job = {
            "id": uuid.uuid4().hex,
            "action": action,
            "state": "fila",
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
        if time.monotonic() >= self.next_history_prune:
            self.persist_history()
            self.next_history_prune = time.monotonic() + 3600
        if self.active is not None or self.closing or self.host_updates.busy:
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
            self.emit("Tentando iniciar o servidor automaticamente")
            self.schedule_once("start")
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
                if job["state"] == "adiado":
                    job["state"] = "fila"
                    self.queue.put_nowait(job)

    async def shutdown(self):
        self.closing = True
        self.want_running = False
        self.emit(
            "Desligamento do contêiner solicitado; suspendendo tarefas e preparando parada do jogo"
        )
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
                if queued["state"] != "cancelado":
                    queued["state"] = "adiado" if queued.get("scheduled") else "cancelado"
                self.queue.task_done()
        if self.active and self.active["action"] != "check-update":
            self.emit(
                f"Aguardando operação {self.active['action']} em andamento por até {SHUTDOWN_JOB_TIMEOUT}s"
            )
            try:
                await asyncio.wait_for(self.queue.join(), timeout=SHUTDOWN_JOB_TIMEOUT)
            except asyncio.TimeoutError:
                self.emit("Supervisor aguardava operação ao receber desligamento")
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
        self.emit("Supervisor encerrado")
