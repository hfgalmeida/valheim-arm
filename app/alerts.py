"""Optional Discord notifications; credentials never leave private storage."""

import asyncio
import json
import re
import shutil
import time
import urllib.request

from .config import atomic_json
from .backup_freshness import DEFAULT_HOURS, hours, observe

AGE_KEYS = {"backup_age", "cloud_age"}

MESSAGES = {
    "backup_age": (
        "Backup local atrasado ou indisponível: o prazo configurado foi excedido ou a última cópia disponível foi removida. Consulte o painel.",
        "Backup local novamente dentro do prazo configurado.",
    ),
    "cloud_age": (
        "Envio à nuvem atrasado: nenhum envio concluído dentro do prazo configurado. Consulte o painel.",
        "Envio à nuvem novamente dentro do prazo configurado.",
    ),
    "backup": (
        "Falha no backup. Consulte o painel; o backup anterior foi preservado.",
        "Backup normalizado: um novo backup foi concluído.",
    ),
    "cloud": (
        "Falha no envio de backup à nuvem. Consulte a conexão no painel.",
        "Envio de backup à nuvem normalizado.",
    ),
    "game": (
        "Recuperação automática suspensa após falhas consecutivas. Consulte o painel.",
        "Servidor recuperado: mundo carregado e conexão inicializada.",
    ),
    "disk": (
        "Pouco espaço em disco: menos de 6 GiB livres no volume de dados.",
        "Espaço em disco normalizado: pelo menos 7 GiB livres.",
    ),
}


def validate_url(url):
    if not re.fullmatch(r"https://discord\.com/api/webhooks/[0-9]+/[A-Za-z0-9_-]{20,200}", url):
        raise ValueError("Informe uma URL de webhook https://discord.com/api/webhooks/…")
    return url


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Alerts:
    def __init__(self, data):
        self.data = data
        self.path = data / "config/alerts.json"
        self.state_path = data / "config/alert-state.json"
        self.age_path = data / "config/backup-age-state.json"
        self.settings, self.incidents = {}, {}
        self.error = None
        self.last_sent = None
        self.lock = asyncio.Lock()
        self.test_after = 0
        self.freshness = {}
        self.freshness_lock = asyncio.Lock()
        try:
            self.settings = json.loads(self.path.read_text()) if self.path.exists() else {}
            self.incidents = (
                json.loads(self.state_path.read_text()) if self.state_path.exists() else {}
            )
            if not isinstance(self.settings, dict) or not isinstance(self.incidents, dict):
                raise ValueError
            ages = json.loads(self.age_path.read_text()) if self.age_path.exists() else {}
            if not isinstance(ages, dict) or any(key not in AGE_KEYS for key in ages):
                raise ValueError
            self.incidents.update(ages)
            if self.settings.get("url"):
                validate_url(self.settings["url"])
            for key in ("backup_max_age_hours", "cloud_max_age_hours"):
                hours(self.settings.get(key, DEFAULT_HOURS))
            for key, value in self.incidents.items():
                if key not in MESSAGES or not isinstance(value, dict):
                    raise ValueError
        except (OSError, ValueError, TypeError):
            self.settings, self.incidents = {}, {}
            self.error = "Estado de alertas inválido; reconfigure no painel."

    def persist_incidents(self, records):
        # Keep the original state schema readable when rolling back the image.
        atomic_json(
            self.state_path, {key: value for key, value in records.items() if key not in AGE_KEYS}
        )
        atomic_json(
            self.age_path, {key: value for key, value in records.items() if key in AGE_KEYS}
        )

    def describe(self):
        return {
            "enabled": bool(self.settings.get("enabled")),
            "configured": bool(self.settings.get("url")),
            "error": self.error,
            "last_sent": self.last_sent,
            "active": [key for key, value in self.incidents.items() if value.get("active")],
            "backup_max_age_hours": self.settings.get("backup_max_age_hours", DEFAULT_HOURS),
            "cloud_max_age_hours": self.settings.get("cloud_max_age_hours", DEFAULT_HOURS),
            "freshness": self.freshness,
        }

    def configure(self, enabled, url="", backup_max_age_hours=None, cloud_max_age_hours=None):
        if self.lock.locked():
            raise ValueError("Aguarde o envio em andamento.")
        url = validate_url(url.strip()) if url.strip() else self.settings.get("url", "")
        if enabled and not url:
            raise ValueError("Informe o webhook antes de ativar os alertas.")
        settings = {"enabled": enabled, "url": url}
        for key, value in (
            ("backup_max_age_hours", backup_max_age_hours),
            ("cloud_max_age_hours", cloud_max_age_hours),
        ):
            settings[key] = hours(self.settings.get(key, DEFAULT_HOURS) if value is None else value)
        atomic_json(self.path, settings)
        self.settings = settings
        self.error = None
        self.freshness = {}
        return self.describe()

    async def check_freshness(self, runtime, cloud=None):
        async with self.freshness_lock:
            now = time.time()
            local_error = False
            try:
                local = await asyncio.to_thread(runtime.backups.latest_completed, now)
            except OSError:
                local, local_error = None, True
            cloud = runtime.cloud.describe() if cloud is None else cloud
            statuses, updated = {}, dict(self.incidents)
            for name, key, latest, enabled, origin in (
                ("local", "backup_age", local, True, None),
                (
                    "cloud",
                    "cloud_age",
                    {"time": cloud.get("last_success"), "name": cloud.get("last_file")},
                    bool(cloud.get("enabled")),
                    cloud.get("monitor_since"),
                ),
            ):
                limit = self.settings.get(
                    "backup_max_age_hours" if name == "local" else "cloud_max_age_hours",
                    DEFAULT_HOURS,
                )
                if name == "local" and local_error:
                    _, updated[key] = observe(updated.get(key, {}), None, now, limit)
                    statuses[name] = {
                        "last_success": None,
                        "last_file": None,
                        "max_age_hours": limit,
                        "state": "disabled" if limit == 0 else "unknown",
                        "checked_at": now,
                        "age_seconds": None,
                        "due_at": None,
                        "error": "Não foi possível consultar os backups locais; confira espaço e permissões.",
                    }
                    continue
                statuses[name], updated[key] = observe(
                    updated.get(key, {}), latest, now, limit, enabled, origin
                )
            if updated != self.incidents:
                self.persist_incidents(updated)
                self.incidents = updated
            self.freshness = statuses
            return statuses

    def condition(self, key, active):
        previous = self.incidents.get(key, {})
        if bool(previous.get("active")) == active:
            return
        updated = self.incidents | {key: previous | {"active": active, "changed": time.time()}}
        self.persist_incidents(updated)
        self.incidents = updated

    def post(self, content):
        # A fixed HTTPS destination and disabled redirects prevent arbitrary URL requests.
        url = validate_url(self.settings["url"]) + "?wait=true"
        payload = json.dumps(
            {"content": content[:2000], "allowed_mentions": {"parse": []}}
        ).encode()
        request = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "ValheimARM/1.0"},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=10) as response:
                if response.status not in (200, 204):
                    raise ValueError
        except Exception:
            raise ValueError(
                "Não foi possível entregar ao Discord. Verifique o webhook e a conexão."
            ) from None

    async def test(self):
        if not self.settings.get("url"):
            raise ValueError("Salve um webhook antes de testar.")
        if self.lock.locked() or time.monotonic() < self.test_after:
            raise ValueError("Aguarde 60 segundos entre testes.")
        self.test_after = time.monotonic() + 60
        async with self.lock:
            await asyncio.to_thread(
                self.post, "Valheim ARM · Teste de notificações recebido com sucesso."
            )
        return {"sent": True}

    async def poll(self, runtime):
        cloud = runtime.cloud.describe()
        await self.check_freshness(runtime, cloud)
        if cloud["enabled"]:
            if cloud["error"]:
                self.condition("cloud", True)
            elif (cloud.get("last_success") or 0) >= self.incidents.get("cloud", {}).get(
                "changed", 0
            ):
                self.condition("cloud", False)
        if runtime.recovery_error:
            self.condition("game", True)
        elif runtime.live and runtime.game_ready:
            self.condition("game", False)
        free = shutil.disk_usage(self.data).free
        if free < 6 * 1024**3:
            self.condition("disk", True)
        elif free >= 7 * 1024**3:
            self.condition("disk", False)
        if not self.settings.get("enabled"):
            return
        async with self.lock:
            for key, incident in list(self.incidents.items()):
                active, notified = bool(incident.get("active")), bool(incident.get("notified"))
                if active == notified or time.time() < incident.get("next_send", 0):
                    continue
                # At least five minutes between notifications for the same incident.
                try:
                    title = "🔴 Valheim ARM" if active else "🟢 Valheim ARM"
                    await asyncio.to_thread(
                        self.post, title + " · " + MESSAGES[key][0 if active else 1]
                    )
                    # Preserve conditions that changed while the network request was in flight.
                    self.incidents[key] = self.incidents[key] | {
                        "notified": active,
                        "next_send": time.time() + 300,
                    }
                    self.last_sent, self.error = time.time(), None
                except ValueError as exc:
                    self.incidents[key] = self.incidents[key] | {"next_send": time.time() + 300}
                    self.error = str(exc)
                self.persist_incidents(self.incidents)

    async def run(self, runtime):
        while True:
            try:
                await self.poll(runtime)
            except Exception:
                self.error = (
                    "Falha na verificação dos alertas; nova tentativa automática em 30 segundos."
                )
            await asyncio.sleep(30)
