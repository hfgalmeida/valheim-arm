"""Bounded maintenance records, separate from the queue that may be resumed."""

import json
import math
import time

from .config import atomic_json
from .compatibility import operation_record

LIMIT = 200
RETENTION = 30 * 86400
ACTIVE = {"queued", "running", "deferred"}
FIELDS = {
    "actor",
    "audit_request_id",
    "requires_idle",
    "id",
    "action",
    "state",
    "scheduled",
    "origin",
    "created",
    "started",
    "finished",
    "duration",
    "attempts",
    "last_attempt_finished",
    "result",
    "error",
    "backup",
    "backup_created",
}


def origin(job):
    if not job.get("scheduled"):
        return "operator" if job.get("actor", {}).get("role") == "operator" else "administrator"
    return "recovery" if job["action"] in ("start", "recover-startup") else "schedule"


class MaintenanceHistory:
    def __init__(self, data):
        self.path = data / "config/maintenance-history.json"
        self.error = None
        self.cached = None
        self.load_error = None

    def load(self):
        try:
            if not self.path.exists():
                return []
            if self.path.stat().st_size > 2 * 1024**2:
                raise ValueError("History too large")
            records = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(records, list) or any(
                not isinstance(j, dict)
                or not all(isinstance(j.get(k), str) for k in ("id", "action", "state"))
                or not isinstance(j.get("created"), (int, float))
                or not math.isfinite(j["created"])
                for j in records
            ):
                raise ValueError("Invalid history")
            return [operation_record({k: v for k, v in j.items() if k in FIELDS}) for j in records]
        except (OSError, ValueError):
            self.load_error = self.error = (
                "Could not read maintenance history; check the files on the host."
            )
            return []

    def save(self, jobs, secrets=()):
        records = []
        for job in jobs:
            record = {k: v for k, v in job.items() if k in FIELDS}
            for field in ("error", "result"):
                if isinstance(record.get(field), str):
                    for secret in secrets:
                        if secret:
                            record[field] = record[field].replace(secret, "[password]")
                    record[field] = record[field][:1000]
            records.append(record)
        try:
            text = json.dumps(records, sort_keys=True)
            if self.cached != text:
                atomic_json(self.path, records)
                self.cached = text
            self.error = self.load_error
        except OSError:
            self.error = "Could not save maintenance history; check disk space and permissions."


def prune(jobs):
    cutoff = time.time() - RETENTION
    terminal = [j for j in jobs if j["state"] not in ACTIVE and j.get("created", 0) >= cutoff]
    keep = {j["id"] for j in terminal[-LIMIT:]}
    return [j for j in jobs if j["state"] in ACTIVE or j["id"] in keep]
