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

from .backups import Backups, digest
from .cloud import Cloud
from .config import ConfigStore, atomic_json
from .players import Players, query_steam

log = logging.getLogger("valheim")
GAME_STOP_TIMEOUT = 150
SHUTDOWN_JOB_TIMEOUT = 120
STOP_PROGRESS_INTERVAL = 15
LOG_DRAIN_TIMEOUT = 2


class SteamError(RuntimeError):
    def __init__(self, code, output):
        self.code, self.output = code, output
        super().__init__(f"SteamCMD falhou (código de saída {code}); consulte os logs e /data/home/Steam/logs/stderr.txt")


def public_build(output):
    match = re.search(r'"896660"\s*\{', output)
    if not match:
        raise RuntimeError("Metadados do app não encontrados")
    tokens = iter(re.findall(r'"([^"\n]*)"|([{}])', output[match.end():]))

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
    result = subprocess.run(["FEXInterpreter", "/usr/bin/env", "VALHEIM_PREFLIGHT=1", "/usr/bin/uname", "-m"], capture_output=True, text=True, timeout=30)
    if result.returncode or "x86_64" not in result.stdout:
        raise RuntimeError("Smoke test do FEX falhou; confira kernel, rootfs e logs: " + result.stderr[-1000:])


def guest_library_path(install):
    root = Path(os.environ.get("FEX_ROOTFS", "/opt/rootfs"))
    return ":".join(map(str, (install / "linux64", install,
        install / "valheim_server_Data/Plugins", install / "valheim_server_Data/Plugins/x86_64",
        root / "usr/lib/x86_64-linux-gnu", root / "lib/x86_64-linux-gnu",
        root / "usr/lib/x86_64-linux-gnu/pulseaudio")))


def check_crossplay(install):
    root = Path(os.environ.get("FEX_ROOTFS", "/opt/rootfs"))
    loader = root / "usr/lib/x86_64-linux-gnu/ld-linux-x86-64.so.2"
    libraries = sorted(p for p in (install / "valheim_server_Data/Plugins").rglob("*.so")
                       if p.name.lower() == "libparty.so")
    if not libraries:
        raise RuntimeError("Crossplay: biblioteca Party não encontrada na instalação do Valheim")
    env = os.environ.copy()
    env["FEX_ROOTFS"] = str(root)
    env.pop("LD_TRACE_LOADED_OBJECTS", None)
    for library in libraries:
        paths = guest_library_path(install) + ":" + str(library.parent)
        result = subprocess.run(["FEXInterpreter", str(loader), "--library-path", paths, "--list", str(library)],
                                env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise RuntimeError("Crossplay: dependência x86 ausente ou incompatível; atualize a imagem Docker. " + result.stderr[-2000:])


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


def install_bepinex(data, install, settings):
    if not settings.bepinex:
        return
    mods = data / "mods"
    marker = mods / "pack.json"
    expected = settings.bepinex_sha256.lower()
    current = json.loads(marker.read_text()) if marker.exists() else {}
    if current.get("sha256") != expected:
        with tempfile.TemporaryDirectory(dir=data) as tmp:
            archive = Path(tmp) / "bepinex.zip"
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
                    if ".." in parts or item.filename.startswith(("/", "\\")) or "\\" in item.filename or ":" in item.filename:
                        raise ValueError("Caminho inseguro no pacote BepInEx")
                    if (item.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Links não permitidos no pacote BepInEx")
                pack.extractall(unpack)
            roots = [p.parent for p in unpack.rglob("libdoorstop.so") if p.parent.joinpath("BepInEx/core").is_dir()]
            if len(roots) != 1:
                raise ValueError("Use BepInExPack_Valheim Linux com libdoorstop.so e BepInEx/core")
            source = roots[0]
            for directory in ("plugins", "config", "patchers"):
                (mods / "BepInEx" / directory).mkdir(parents=True, exist_ok=True)
            shutil.copytree(source / "BepInEx/core", mods / "BepInEx/core", dirs_exist_ok=True)
            shutil.copy2(source / "libdoorstop.so", mods / "libdoorstop.so")
            atomic_json(marker, {"sha256": expected, "url": settings.bepinex_url})
    for name, destination in (("BepInEx", mods / "BepInEx"), ("libdoorstop.so", mods / "libdoorstop.so")):
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
        self.backups = Backups(data)
        self.cloud = Cloud(data, self.backups)
        self.cloud_task = None
        self.players = Players()
        self.process = None
        self.reader = None
        self.running = None
        self.join_code = None
        self.state = "parado"
        self.logs = deque(maxlen=500)
        self.jobs = deque()
        self.queue = asyncio.Queue(maxsize=32)
        self.active = None
        self.closing = False
        self.lock_file = None
        self.worker_task = None
        self.scheduler_task = None
        self.last_update = 0
        self.latest_build = None
        self.next_retry = 0
        self.want_running = True
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
            job["state"] = "fila"
            self.jobs.append(job)
            self.queue.put_nowait(job)

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
        for password in {self.config.effective().server_password, self.running.server_password if self.running else ""}:
            if password:
                message = message.replace(password, "[senha]")
        self.logs.append({"time": time.time(), "message": message[:4000]})
        log.info("%s", message[:4000])

    def status(self):
        crossplay = (self.running if self.live and self.running else self.config.effective()).crossplay
        return {"state": self.state, "running": self.live, "players": self.players.status() if self.live else {"count": 0, "reason": "Servidor parado"},
                "crossplay": crossplay, "join_code": self.join_code if self.live and crossplay else None,
                "pending_config": self.running is not None and self.running != self.config.effective(),
                "installed_build": self.installed_build(), "available_build": self.latest_build,
                "maintenance_persistence_error": self.persistence_error,
                "automatic_recovery": self.want_running and not self.closing,
                "cloud": self.cloud.describe(), "jobs": list(self.jobs), "logs": list(self.logs)}

    def submit(self, action, backup=None):
        if action not in ("start", "stop", "restart", "update", "backup", "restore"):
            raise ValueError("Ação desconhecida")
        if action == "restore":
            self.backups.resolve(backup or "")
        job = {"id": uuid.uuid4().hex, "action": action, "backup": backup, "state": "fila", "created": time.time()}
        self.queue.put_nowait(job)
        self.jobs.append(job)
        self.trim_jobs()
        return job

    def trim_jobs(self):
        if len(self.jobs) > 100:
            terminal = [j for j in self.jobs if j["state"] not in ("fila", "executando", "adiado")]
            for job in terminal[:len(self.jobs)-100]:
                self.jobs.remove(job)

    def persist_pending(self):
        try:
            atomic_json(self.pending_path, [j for j in self.jobs if j.get("scheduled") and j["state"] in ("fila", "executando", "adiado")])
            self.persistence_error = None
        except OSError:
            self.persistence_error = "Não foi possível persistir manutenção; confira espaço e permissões"
            self.emit(self.persistence_error)

    async def boot(self):
        import fcntl
        self.lock_file = (self.data / "config/supervisor.lock").open("a")
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Outro supervisor já utiliza este volume")
        await asyncio.to_thread(preflight, self.data)
        self.worker_task = asyncio.create_task(self.worker())
        self.scheduler_task = asyncio.create_task(self.scheduler())
        self.cloud_task = asyncio.create_task(self.cloud.run())
        self.submit("start")

    async def run_steam(self, args, timeout=1800):
        await asyncio.to_thread(install_steam, self.data)
        steam = self.data / "steamcmd"
        command = ["FEXInterpreter", "/usr/bin/env", "LD_LIBRARY_PATH=" + str(steam / "linux32"),
                   str(steam / "linux32/steamcmd"), "+@sSteamCmdForcePlatformType", "linux", *args]
        lines = []

        async def run():
            # Valve's steamcmd.sh re-executes on 42 after a self-update. We run
            # the binary through FEX, so handle that restart here, with a limit.
            for attempt in range(4):
                first_line = len(lines)
                child = await asyncio.create_subprocess_exec(*command, cwd=steam,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True)
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
                    self.emit("SteamCMD solicitou reinício após autoatualização (código 42); executando novamente")
                    continue
                if code:
                    raise SteamError(code, "\n".join(lines[first_line:]))
                return "\n".join(lines)

        # One deadline covers the download and all self-update restarts.
        return await asyncio.wait_for(run(), timeout)

    async def available_build(self):
        output = await self.run_steam(["+login", "anonymous", "+app_info_update", "1", "+app_info_print", "896660", "+quit"], timeout=180)
        return public_build(output)

    async def fetch_game(self, stage):
        # Finish SteamCMD's bootstrap before asking it to install the game.
        await self.run_steam(["+quit"], timeout=180)

        async def fetch():
            for attempt in range(3):
                try:
                    output = await self.run_steam(["+force_install_dir", str(stage), "+login", "anonymous",
                                                  "+app_info_update", "1", "+app_update", "896660", "validate", "+quit"])
                    if "Failed to install app '896660' (Missing configuration)" not in output:
                        return output
                    raise SteamError(0, output)
                except SteamError as exc:
                    missing = "Failed to install app '896660' (Missing configuration)" in exc.output
                    if not missing or exc.code not in (0, 8) or attempt == 2:
                        raise
                    # Invalidate only disposable app metadata, never saves,
                    # downloaded depots, credentials or the current install.
                    for cache in (self.data / "steamcmd/appcache/appinfo.vdf",
                                  self.data / "home/Steam/appcache/appinfo.vdf"):
                        cache.unlink(missing_ok=True)
                    delay = 10 * (attempt + 1)
                    self.emit(f"SteamCMD: Missing configuration; metadados serão consultados novamente em {delay}s (tentativa {attempt + 2}/3)")
                    await asyncio.sleep(delay)

        return await asyncio.wait_for(fetch(), timeout=1800)

    async def install(self):
        self.state = "instalando"
        stage = self.data / "installs" / uuid.uuid4().hex
        stage.mkdir()
        try:
            output = await self.fetch_game(stage)
            if "Success! App '896660' fully installed" not in output or not (stage / "valheim_server.x86_64").is_file():
                raise RuntimeError("SteamCMD não confirmou instalação válida")
            # All game processes are stopped before this method is called.
            pointer = self.data / ".next-current"
            pointer.unlink(missing_ok=True)
            pointer.symlink_to(stage, target_is_directory=True)
            pointer.replace(self.data / "current")
            # Keep current + immediately previous install, not an unbounded history.
            installs = sorted((self.data / "installs").iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
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
        self.join_code = None
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
            guest_env += ["LD_PRELOAD=" + str(install / "libdoorstop.so"), "DOORSTOP_ENABLE=TRUE",
                          "DOORSTOP_ENABLED=1", "DOORSTOP_TARGET_ASSEMBLY=" + str(install / "BepInEx/core/BepInEx.Preloader.dll"),
                          "DOORSTOP_INVOKE_DLL_PATH=" + str(install / "BepInEx/core/BepInEx.Preloader.dll")]
        self.players.reset()
        self.state = "iniciando"
        self.stop_request = None
        self.running = settings
        self.clean_stop = False
        atomic_json(self.clean_marker, {"started": time.time(), "world": settings.world_name})
        self.process = await asyncio.create_subprocess_exec("FEXInterpreter", "/usr/bin/env", *guest_env,
                        str(install / "valheim_server.x86_64"), *settings.arguments(self.data / "saves"), cwd=install,
                        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, start_new_session=True, limit=1024*1024)
        self.reader = asyncio.create_task(self.read_game(self.process))

    async def read_game(self, process):
        try:
            while line := await process.stdout.readline():
                text = line.decode(errors="replace").strip()
                self.players.consume(text)
                if self.running and self.running.crossplay:
                    match = re.search(r'\bSession ".*" (?:registered )?with join code ([0-9]{6})(?![0-9])', text)
                    if match:
                        self.join_code = match[1]
                if "World saved" in text:
                    self.last_save = time.monotonic()
                if "Game server connected" in text and "error" not in text.lower():
                    self.state = "online"
                self.emit(text)
        except Exception as exc:
            self.players.reset()
            self.join_code = None
            self.emit("Leitura do log falhou: " + str(exc))
        finally:
            self.join_code = None
            code = await process.wait()
            self.state = "parado" if code in (0, -signal.SIGINT) else "falha"
            self.players.reset()
            self.emit(f"Processo do jogo encerrado: {code}")
            self.next_recovery = time.monotonic() + 300

    async def stop(self, safe=True):
        process = self.process
        pending = self.stop_request is not None and self.stop_request[0] is process
        if not self.live and not pending:
            if not self.clean_stop:
                raise RuntimeError("O jogo terminou sem parada limpa; inicie e encerre corretamente antes de manter os saves")
            return
        if not pending:
            if safe:
                if not self.players.idle():
                    raise RuntimeError("Aguardando zero jogadores confirmado por dois minutos")
                if not self.running.crossplay:
                    count = await asyncio.to_thread(query_steam, self.running.server_port + 1)
                    if count != 0:
                        raise RuntimeError("Consulta Steam divergente ou indisponível")
            pid = process.pid
            if safe:
                # Scheduled/panel operations still freeze admissions and drain
                # captured connection events before requesting a clean save.
                os.killpg(pid, signal.SIGSTOP)
            try:
                if safe:
                    await asyncio.sleep(0.25)
                    if not self.players.idle():
                        raise RuntimeError("Conexão detectada na verificação final; parada cancelada")
                requested = time.monotonic()
                os.killpg(pid, signal.SIGINT)
                self.stop_request = (process, requested)
                self.state = "parando"
                self.emit(f"SIGINT enviado ao jogo; aguardando salvamento e saída por até {GAME_STOP_TIMEOUT}s")
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
                raise RuntimeError(f"Parada excedeu {GAME_STOP_TIMEOUT}s; jogo não foi morto, backup/reinício cancelados")
            if time.monotonic() >= next_progress:
                self.emit(f"Aguardando o jogo salvar e encerrar; restam até {remaining:.0f}s")
                next_progress = time.monotonic() + STOP_PROGRESS_INTERVAL
            await asyncio.wait([reader], timeout=min(1, remaining))
        if not reader.cancelled():
            await reader
        if process.returncode not in (0, -signal.SIGINT):
            raise RuntimeError("Saída anormal; backup/atualização cancelados")
        if self.last_save < requested:
            raise RuntimeError("Saída sem confirmação World saved; backup/atualização cancelados")
        self.clean_stop = True
        self.state = "parado"
        self.clean_marker.unlink(missing_ok=True)
        self.emit("Jogo encerrado com salvamento confirmado")

    async def snapshot(self, reason):
        if self.live:
            raise RuntimeError("Backup consistente exige servidor parado")
        if not self.clean_stop:
            raise RuntimeError("Backup cancelado: último encerramento não foi confirmado como limpo")
        name = await asyncio.to_thread(self.backups.create, reason, self.running or self.config.effective(), self.installed_build())
        self.cloud.wake.set()
        settings = self.config.effective()
        if reason != "pre-restore":
            await asyncio.to_thread(self.backups.prune, settings.retain_daily, settings.retain_weekly, set(self.cloud.pending()))
        return name

    async def execute(self, job):
        action = job["action"]
        if action == "start":
            if job.get("scheduled") and not self.want_running:
                return
            self.want_running = True
            await self.start()
            return
        if action == "stop" and not self.live:
            self.want_running = False
            return
        if action == "check-update":
            self.latest_build = await self.available_build()
            if self.installed_build() and self.latest_build != self.installed_build():
                self.schedule_once("update")
            return
        if action == "restore":
            if self.live:
                raise RuntimeError("Pare o jogo antes de restaurar")
            await asyncio.to_thread(self.backups.validate, self.backups.resolve(job["backup"]))
            await self.snapshot("pre-restore")
            manifest = await asyncio.to_thread(self.backups.restore, job["backup"])
            job["result"] = "Saves restaurados. Mundo do arquivo: " + manifest["world"]
            return
        was_running = self.live
        await self.stop()
        if action == "stop":
            self.want_running = False
            return
        try:
            job["result"] = await self.snapshot(action)
            if action == "update":
                await self.install()
        finally:
            if was_running and not self.live and not self.closing:
                await self.start()

    async def worker(self):
        while True:
            job = await self.queue.get()
            self.active = job
            job["state"] = "executando"
            job.pop("error", None)
            self.emit("Executando operação: " + job["action"])
            try:
                if job.get("scheduled"):
                    self.persist_pending()
                    if self.persistence_error:
                        raise RuntimeError(self.persistence_error)
                await self.execute(job)
                job["state"] = "concluído"
            except Exception as exc:
                job["state"], job["error"] = "falha", str(exc)
                self.emit(("Operação adiada" if job.get("scheduled") and job["action"] in ("restart", "backup", "update") else "Operação falhou") + " (" + job["action"] + "): " + str(exc))
                if job["action"] == "start":
                    self.next_recovery = time.monotonic() + 300
                    self.emit("Nova tentativa de inicialização em cinco minutos")
                if job.get("scheduled") and job["action"] in ("restart", "backup", "update"):
                    job["state"] = "adiado"
                    self.next_retry = time.monotonic() + 300
            finally:
                self.active = None
                self.persist_pending()
                self.queue.task_done()

    def schedule_once(self, action):
        if action in ("restart", "backup", "update"):
            for pending in self.jobs:
                if pending.get("scheduled") and pending["state"] in ("fila", "adiado") and pending["action"] in ("restart", "backup", "update"):
                    priorities = {"backup": 0, "restart": 1, "update": 2}
                    pending["action"] = max((action, pending["action"]), key=priorities.get)
                    self.persist_pending()
                    return
        if any(j["action"] == action and j["state"] in ("fila", "executando", "adiado") for j in self.jobs):
            return
        job = {"id": uuid.uuid4().hex, "action": action, "state": "fila", "scheduled": True, "created": time.time()}
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
        if self.active is not None or self.closing:
            return
        if not self.live and self.want_running and self.queue.empty() and time.monotonic() >= self.next_recovery:
            self.next_recovery = time.monotonic() + 300
            self.emit("Tentando iniciar o servidor automaticamente")
            self.schedule_once("start")
        settings = self.config.effective()
        now = datetime.now(ZoneInfo(settings.timezone))
        if self.live:
            # Query first: a newly found update can absorb the queued daily
            # restart/backup before that maintenance operation starts.
            if settings.auto_update and time.monotonic() - self.last_update >= settings.update_interval:
                self.last_update = time.monotonic()
                self.schedule_once("check-update")
            due = []
            for action, hour in (("restart", settings.restart_at), ("backup", settings.backup_at)):
                key = action + ":" + now.date().isoformat()
                if hour != "off" and now.strftime("%H:%M") >= hour and key not in self.daily:
                    due.append((action, key))
            if due:
                # restart already includes snapshot, coalesce daily tasks.
                self.schedule_once("restart" if any(action == "restart" for action, _ in due) else "backup")
                self.daily.update({key: True for _, key in due})
                self.daily = {k: v for k, v in self.daily.items() if k.split(":", 1)[1] >= now.date().isoformat()}
                atomic_json(self.daily_path, self.daily)
        if time.monotonic() >= self.next_retry:
            for job in self.jobs:
                if job["state"] == "adiado":
                    job["state"] = "fila"
                    self.queue.put_nowait(job)

    async def shutdown(self):
        self.closing = True
        self.want_running = False
        self.emit("Desligamento do contêiner solicitado; suspendendo tarefas e preparando parada do jogo")
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
                queued["state"] = "adiado" if queued.get("scheduled") else "cancelado"
                self.queue.task_done()
        if self.active and self.active["action"] != "check-update":
            self.emit(f"Aguardando operação {self.active['action']} em andamento por até {SHUTDOWN_JOB_TIMEOUT}s")
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
