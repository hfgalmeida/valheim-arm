import asyncio
import json
import time
from unittest.mock import patch

from app.maintenance_history import MaintenanceHistory, prune
from app.runtime import MaintenanceDeferred, Runtime


def run_jobs(runtime):
    async def run():
        task = asyncio.create_task(runtime.worker())
        await runtime.queue.join()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())


def test_completed_history_survives_without_replaying_actions(runtime):
    async def execute(job):
        job["backup_created"] = "test.tar.gz"
        job["result"] = "ok"

    runtime.execute = execute
    job = runtime.submit("restart")
    run_jobs(runtime)
    assert job["started"] <= job["finished"]
    assert job["duration"] >= 0
    assert job["attempts"] == 1
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.empty()
    assert restored.jobs[0]["id"] == job["id"]
    assert restored.jobs[0]["backup_created"] == "test.tar.gz"
    assert restored.jobs[0]["origin"] == "administrator"
    assert restored.jobs[0]["state"] == "completed"


def test_deferred_attempt_is_not_finished_and_keeps_origin(runtime):
    async def execute(job):
        raise MaintenanceDeferred("Players online")

    runtime.execute = execute
    runtime.schedule_once("restart")
    run_jobs(runtime)
    job = runtime.jobs[0]
    assert job["state"] == "deferred"
    assert "finished" not in job
    assert job["last_attempt_finished"] >= job["started"]
    assert job["origin"] == "schedule"

    async def success(job):
        pass

    runtime.execute = success
    runtime.queue = asyncio.Queue(maxsize=32)
    job["state"] = "queued"
    runtime.queue.put_nowait(job)
    run_jobs(runtime)
    assert job["attempts"] == 2
    assert job["finished"] >= job["started"]
    assert "error" not in job


def test_interrupted_manual_operation_is_never_replayed(runtime):
    job = runtime.submit("restart")
    job["state"] = "running"
    runtime.persist_history()
    restored = Runtime(runtime.data, runtime.config)
    assert restored.queue.empty()
    assert restored.jobs[0]["state"] == "failed"
    assert "interrupted" in restored.jobs[0]["error"]


def test_retention_preserves_pending_and_limits_terminal_records():
    now = time.time()
    jobs = [{"id": str(i), "state": "completed", "created": now} for i in range(250)]
    jobs += [
        {"id": "pending", "state": "deferred", "created": 1},
        {"id": "expired", "state": "failed", "created": 1},
    ]
    result = prune(jobs)
    assert len(result) == 201
    assert result[-1]["id"] == "pending"
    assert result[0]["id"] == "50"


def test_history_failure_does_not_stop_operations_and_redacts_password(runtime):
    async def execute(job):
        raise RuntimeError("Failed: " + runtime.config.effective().server_password)

    runtime.execute = execute
    runtime.submit("restart")
    with patch("app.maintenance_history.atomic_json", side_effect=OSError("disk full")):
        run_jobs(runtime)
    assert runtime.jobs[0]["state"] == "failed"
    assert runtime.status()["maintenance_history_error"]
    runtime.persist_history()
    text = runtime.maintenance_history.path.read_text()
    assert runtime.config.effective().server_password not in text
    assert "[password]" in text
    assert runtime.maintenance_history.error is None


def test_corrupt_history_does_not_break_startup(tmp_path):
    history = MaintenanceHistory(tmp_path)
    history.path.parent.mkdir(parents=True)
    history.path.write_text("invalid json")
    assert history.load() == []
    assert history.error


def test_recovery_origin_and_cancelled_requests_are_persisted(runtime):
    runtime.schedule_once("recover-startup")
    runtime.submit("stop")
    records = json.loads(runtime.maintenance_history.path.read_text())
    recovery = next(j for j in records if j["action"] == "recover-startup")
    assert recovery["origin"] == "recovery"
    assert recovery["state"] == "cancelled"
    assert recovery["finished"] >= recovery["created"]


def test_hourly_prune_expires_terminal_records_without_new_operations(runtime):
    job = runtime.submit("restart")
    job["state"] = "completed"
    job["created"] = time.time() - 31 * 86400
    runtime.next_history_prune = 0
    runtime.closing = True
    asyncio.run(runtime.tick())
    assert not runtime.jobs
    assert json.loads(runtime.maintenance_history.path.read_text()) == []
