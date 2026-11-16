"""Private bounded audit trail. Never persist request bodies or raw errors."""

import json
import logging
import time
import threading
import uuid

from .config import atomic_json

log = logging.getLogger("valheim")
LIMIT = 2000
MAX_BYTES = 8 * 1024**2
RETENTION = 90 * 86400
SYSTEM = {"id": "system", "username": "automático", "role": "system"}


class Audit:
    def __init__(self, data):
        self.lock = threading.RLock()
        self.path = data / "config/audit.json"
        self.error = None
        self.records = []
        self.job_states = {}
        try:
            if self.path.exists():
                if self.path.stat().st_size > 16 * 1024**2:
                    raise ValueError("Arquivo excessivo")
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, list) or any(
                    not isinstance(v, dict) or not isinstance(v.get("time"), (int, float))
                    for v in value
                ):
                    raise ValueError("Auditoria inválida")
                self.records = value[-LIMIT:]
        except (ValueError, OSError):
            self.error = "Auditoria indisponível; confira espaço, permissões e integridade no host"
            self.corrupt = True
        else:
            self.corrupt = False

    @staticmethod
    def actor(value):
        return {key: value.get(key) for key in ("id", "username", "role")}

    def append(self, *args, **kwargs):
        with self.lock:
            return self._append(*args, **kwargs)

    def _append(
        self,
        actor,
        operation,
        outcome,
        resource="",
        request_id=None,
        job_id=None,
        changes=None,
        kind="request",
    ):
        if self.corrupt:
            raise OSError(self.error)
        entry = {
            "kind": kind,
            "id": uuid.uuid4().hex,
            "time": time.time(),
            "actor": self.actor(actor or SYSTEM),
            "operation": operation,
            "resource": resource,
            "outcome": outcome,
            "request_id": request_id,
            "job_id": job_id,
        }
        if changes:
            entry["changes"] = changes
        cutoff = time.time() - RETENTION
        records = [r for r in self.records if r["time"] >= cutoff][-LIMIT + 1 :] + [entry]
        while len(json.dumps(records, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_BYTES:
            if len(records) <= 1:
                self.error = "Registro de auditoria excessivo"
                raise OSError(self.error)
            records = records[max(1, len(records) // 10) :]
        try:
            atomic_json(self.path, records)
        except OSError as exc:
            log.error("Falha ao gravar auditoria: %s", type(exc).__name__)
            self.error = "Não foi possível gravar a auditoria; confira espaço e permissões"
            raise
        self.records, self.error = records, None
        return entry

    def seed(self, jobs):
        previous = {
            r["job_id"]: r["outcome"]
            for r in self.records
            if r.get("kind") == "job" and r.get("job_id")
        }
        self.job_states = {
            j["id"]: (previous.get(j["id"], j["state"]), j.get("attempts", 0)) for j in jobs
        }

    def sync_jobs(self, jobs):
        jobs = list(jobs)
        for job in jobs:
            signature = (job["state"], job.get("attempts", 0))
            if self.job_states.get(job["id"]) == signature:
                continue
            if not job.get("actor") and not job.get("scheduled"):
                continue
            try:
                self.append(
                    job.get("actor") or SYSTEM,
                    job["action"],
                    job["state"],
                    job.get("backup_created") or job.get("backup") or "servidor",
                    job.get("audit_request_id"),
                    job["id"],
                    kind="job",
                )
            except OSError:
                log.error("Falha ao persistir auditoria de manutenção")
                continue
            self.job_states[job["id"]] = signature
        visible = {job["id"] for job in jobs}
        self.job_states = {key: value for key, value in self.job_states.items() if key in visible}

    def query(self, user=None, operation=None, since=None, until=None, limit=100):
        return [
            r
            for r in reversed(self.records)
            if r["time"] >= time.time() - RETENTION
            and (not user or r.get("actor", {}).get("username") == user)
            and (not operation or r.get("operation") == operation)
            and (since is None or r["time"] >= since)
            and (until is None or r["time"] <= until)
        ][:limit]

    def image_result(self, status):
        outcomes = {
            "succeeded": "concluído",
            "failed": "falha",
            "rolled_back": "revertido",
            "interrupted": "interrompido",
        }
        job_id = status.get("request_id")
        outcome = outcomes.get(status.get("state"))
        if not job_id or not outcome:
            return
        related = [
            r
            for r in self.records
            if r.get("job_id") == job_id and r.get("operation") == "image-update"
        ]
        if not related or any(r["outcome"] == outcome and r.get("kind") == "job" for r in related):
            return
        try:
            self.append(
                related[0]["actor"],
                "image-update",
                outcome,
                "imagem Docker",
                related[0].get("request_id"),
                job_id,
                kind="job",
            )
        except OSError:
            log.error("Falha ao auditar resultado da atualização da imagem")
