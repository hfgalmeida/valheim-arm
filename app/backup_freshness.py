"""Backup-age observations independent of Discord and online player counts."""

import math

DEFAULT_HOURS = 36


def hours(value):
    if type(value) is not int or not 0 <= value <= 720:
        raise ValueError("O prazo deve ser um número inteiro entre 0 e 720 horas")
    return value


def valid_time(value, now):
    return type(value) in (int, float) and math.isfinite(value) and 0 < value <= now


def observe(previous, latest, now, max_hours, enabled=True, origin=None):
    """Return public observation + private incident, without filesystem mutation."""
    max_hours = hours(max_hours)
    latest = latest if latest and valid_time(latest.get("time"), now) else None
    last = latest["time"] if latest else None
    result = {
        "last_success": last,
        "last_file": latest.get("name") if latest else None,
        "max_age_hours": max_hours,
        "age_seconds": int(now - last) if last else None,
        "checked_at": now,
        "due_at": None,
    }
    incident = dict(previous)
    if not enabled or max_hours == 0:
        incident.update(
            active=False,
            notified=False,
            monitor_since=None,
            monitor_generation=None,
            had_success=False,
        )
        result["state"] = "inactive" if not enabled else "disabled"
        return result, incident
    origin = origin if valid_time(origin, now) else None
    generation = {"origin": origin, "hours": max_hours}
    if incident.get("monitor_generation") != generation:
        incident.update(
            active=False,
            notified=False,
            monitor_since=origin or now,
            monitor_generation=generation,
            had_success=False,
            next_send=incident.get("next_send", 0),
        )
    since = incident.get("monitor_since")
    if not valid_time(since, now):
        since = now
        incident["monitor_since"] = since
    missing = latest is None and incident.get("had_success") is True
    if latest:
        incident["had_success"] = True
    due = (last or since) + max_hours * 3600
    overdue = now >= due or missing
    if bool(incident.get("active")) != overdue:
        incident["changed"] = now
    incident["active"] = overdue
    result.update(due_at=due, state="overdue" if overdue else "ok" if latest else "grace")
    return result, incident
