#!/usr/bin/env python3
"""Create a local, redacted support archive. Nothing is uploaded automatically."""

import argparse
import hashlib
import ipaddress
import io
import json
import os
import platform
import re
import shutil
import subprocess
import tarfile
import threading
from datetime import datetime, timezone
from pathlib import Path

MAX_OUTPUT = 1024 * 1024
CONFIG_KEYS = {
    "public",
    "crossplay",
    "preset",
    "save_interval",
    "native_backups",
    "timezone",
    "restart_at",
    "backup_at",
    "auto_update",
    "retain_daily",
    "retain_weekly",
    "bepinex",
    "modifier_combat",
    "modifier_deathpenalty",
    "modifier_resources",
    "modifier_raids",
    "modifier_portals",
    "key_nobuildcost",
    "key_playerevents",
    "key_passivemobs",
    "key_nomap",
}
FEX_KEYS = {
    "FEX_GAME_MODE",
    "FEX_DISKCACHE",
    "FEX_MAXINST",
    "FEX_MULTIBLOCK",
    "FEX_TSOENABLED",
    "FEX_SMCCHECKS",
    "FEX_MONOHACKS",
    "FEX_DISKCACHEMAXFILESIZE",
}


def run(command, timeout=15):
    """Bound memory and runtime even for oversized plugin logs."""
    try:
        child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    except OSError:
        return {"ok": False, "error": "Command unavailable"}
    chunks, total, truncated = [], 0, False

    def drain():
        nonlocal total, truncated
        while chunk := child.stdout.read(65536):
            remaining = MAX_OUTPUT - total
            if remaining > 0:
                chunks.append(chunk[:remaining])
                total += min(len(chunk), remaining)
            if len(chunk) > remaining:
                truncated = True

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    timed_out = False
    try:
        child.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        child.kill()
        child.wait()
    reader.join(timeout=2)
    if not reader.is_alive():
        child.stdout.close()
    if timed_out or child.returncode:
        return {"ok": False, "error": "Command timed out" if timed_out else "Command failed"}
    return {
        "ok": True,
        "output": b"".join(chunks).decode("utf-8", errors="replace"),
        "truncated": truncated or reader.is_alive(),
    }


def json_command(command):
    result = run(command)
    if result["ok"] and not result.get("truncated"):
        try:
            return json.loads(result["output"])
        except ValueError:
            pass
    return None


def local_json(root, relative):
    path = root / relative
    try:
        if (
            path.is_symlink()
            or not path.resolve().is_relative_to(root)
            or path.stat().st_size > MAX_OUTPUT
        ):
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def secrets_from_env(root):
    try:
        path = root / ".env"
        if path.is_symlink() or path.stat().st_size > 65536:
            return []
        values = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not re.match(r"\s*SERVER_PASSWORD\s*=", line):
                continue
            value = line.split("=", 1)[1].strip()
            if value.startswith('"'):
                try:
                    value = json.loads(value)
                except ValueError:
                    value = value.strip('"')
            elif value.startswith("'") and value.endswith("'"):
                value = value[1:-1]
            if value:
                values.append(value.replace("$$", "$"))
        return values
    except OSError:
        return []


def redact(text, secrets=()):
    for secret in sorted(secrets, key=len, reverse=True):
        text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", text)
    text = re.sub(
        r"(?i)authorization\s*[:=]\s*(?:Bearer|Basic)\s+[^\s,;]+", "Authorization=[REDACTED]", text
    )
    text = re.sub(r"https?://[^\s\"'<>]+", "[URL REDACTED]", text, flags=re.I)
    text = re.sub(
        r"(?i)\b(password|passwd|token|authorization|secret|api[_-]?key|access_token|refresh_token)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
        r"\1=[REDACTED]",
        text,
    )
    text = re.sub(r"\$argon2[^\s\"']+", "[HASH REDACTED]", text)
    text = re.sub(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", "[IP REDACTED]", text)

    def ipv6(match):
        try:
            ipaddress.IPv6Address(match[0].split("%", 1)[0])
            return "[IP REDACTED]"
        except ValueError:
            return match[0]

    text = re.sub(r"(?i)(?<!\w)(?:[a-f0-9]{0,4}:){2,}[a-f0-9:.%]+", ipv6, text)
    text = re.sub(
        r"(?i)\b(?:Steam|Xbox|PlayFab|Nintendo|PSN|V|X|M|N|S)_[A-Za-z0-9:._-]+",
        "[PLAYER ID REDACTED]",
        text,
    )
    text = re.sub(r"\b\d{17}\b|(?i:playfab/[A-Za-z0-9]+)", "[PLAYER ID REDACTED]", text)
    text = re.sub(r"(?i)(join code\s*)\d+", r"\1[REDACTED]", text)
    text = re.sub(r"/home/[^/\s:]+", "/home/[USER]", text)
    text = re.sub(r"(?i)[A-Z]:\\Users\\[^\\\s]+", r"C:\\Users\\[USER]", text)
    text = re.sub(r"\b[A-Za-z0-9+/=]{120,}\b", "[OPAQUE DATA REDACTED]", text)
    lines = []
    for line in text.splitlines():
        if re.search(
            r"Got character ZDOID|Got connection SteamID|Got handshake|received local Platform ID|Player connection lost server|Session \"|Player history",
            line,
        ):
            line = "[Player/session identity line omitted]"
        line = re.sub(r"(ZNet\.LoadWorld:).*", r"\1 [WORLD REDACTED]", line)
        line = re.sub(r"(?i)(loading world[: ]).*", r"\1[WORLD REDACTED]", line)
        line = re.sub(r"([\"']).*?\1", "[QUOTED VALUE REDACTED]", line)
        lines.append(re.sub(r"[\x00-\x08\x0b-\x1f\x7f]", "", line))
    return "\n".join(lines)


def sanitize(value, secrets):
    if isinstance(value, dict):
        return {key: sanitize(item, secrets) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    return redact(value, secrets) if isinstance(value, str) else value


def only(value, keys):
    return {key: value[key] for key in keys if isinstance(value, dict) and key in value}


def container_report(service, identifier, include_logs):
    inspected = json_command(["docker", "inspect", identifier])
    if not isinstance(inspected, list) or not inspected or not isinstance(inspected[0], dict):
        return {"service": service, "available": False}
    info = inspected[0]
    state, config, host = (info.get(key, {}) for key in ("State", "Config", "HostConfig"))
    result = {
        "service": service,
        "available": True,
        "image": config.get("Image"),
        "image_id": info.get("Image"),
        "state": only(
            state, ("Status", "ExitCode", "OOMKilled", "StartedAt", "FinishedAt", "Restarting")
        ),
        "health": state.get("Health", {}).get("Status"),
        "limits": only(
            host,
            (
                "Memory",
                "MemorySwap",
                "CpuQuota",
                "CpuPeriod",
                "RestartPolicy",
                "SecurityOpt",
                "CapDrop",
            ),
        ),
        "mounts": [only(mount, ("Type", "Destination", "RW")) for mount in info.get("Mounts", [])],
        "version": only(
            config.get("Labels") or {},
            ("org.opencontainers.image.version", "org.opencontainers.image.revision"),
        ),
        "fex": {
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in config.get("Env", [])
            if "=" in line and line.split("=", 1)[0] in FEX_KEYS
        },
    }
    if service == "valheim":
        result["build"] = only(
            json_command(["docker", "exec", identifier, "cat", "/opt/build-info.json"]),
            (
                "fex_version",
                "fex_commit",
                "architecture",
                "guest_packages",
                "guest_libraries_checked",
            ),
        )
        code = "import json,urllib.request; print(json.dumps(json.load(urllib.request.urlopen('http://127.0.0.1:8080/healthz',timeout=3))))"
        result["supervisor"] = json_command(
            ["docker", "exec", identifier, "/opt/venv/bin/python", "-c", code]
        )
    if include_logs:
        result["recent_logs"] = run(
            ["docker", "logs", "--timestamps", "--since", "1h", "--tail", "200", identifier]
        )
    return result


def collect(root, include_logs=False):
    report = {
        "format": 1,
        "created": datetime.now(timezone.utc).isoformat(),
        "host": {
            "system": platform.system(),
            "kernel": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
            "cpu_count": os.cpu_count(),
        },
        "containers": [],
        "logs_included": include_logs,
    }
    try:
        report["host"]["page_size"] = os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        pass
    report["host"]["disk"] = dict(zip(("total", "used", "free"), shutil.disk_usage(root)))
    for source, keys in (
        ("/proc/meminfo", {"MemTotal", "MemAvailable", "SwapTotal", "SwapFree"}),
        ("/proc/cpuinfo", {"Features"}),
    ):
        try:
            report["host"][Path(source).name] = {
                key.strip(): value.strip()
                for line in Path(source).read_text().splitlines()
                if ":" in line
                for key, value in [line.split(":", 1)]
                if key.strip() in keys
            }
        except OSError:
            pass
    report["docker"] = only(
        json_command(["docker", "info", "--format", "{{json .}}"]),
        (
            "ServerVersion",
            "Architecture",
            "OperatingSystem",
            "KernelVersion",
            "CgroupVersion",
            "Driver",
            "NCPU",
            "MemTotal",
        ),
    )
    report["compose_version"] = (
        run(["docker", "compose", "version", "--short"]).get("output", "unavailable").strip()
    )
    for service in ("valheim", "proxy"):
        result = run(
            [
                "docker",
                "compose",
                "--project-directory",
                str(root),
                "ps",
                "--all",
                "--quiet",
                service,
            ]
        )
        identifiers = result.get("output", "").splitlines()
        if identifiers and re.fullmatch(r"[a-f0-9]{12,64}", identifiers[0]):
            report["containers"].append(container_report(service, identifiers[0], include_logs))
        else:
            report["containers"].append({"service": service, "available": False})
    report["game_overrides"] = only(local_json(root, "data/config/overrides.json"), CONFIG_KEYS)
    for title, filename, keys in (
        (
            "startup_history",
            "data/config/startup-history.json",
            {
                "started",
                "finished",
                "ready_at",
                "profile",
                "crossplay",
                "bepinex",
                "phase",
                "outcome",
                "signal",
                "signal_code",
                "signature",
                "exit_code",
                "fex_version",
            },
        ),
        (
            "maintenance_history",
            "data/config/maintenance-history.json",
            {"action", "state", "origin", "created", "started", "finished", "attempts", "duration"},
        ),
    ):
        records = local_json(root, filename)
        if isinstance(records, list):
            report[title] = [
                only(record, keys) for record in records[-20:] if isinstance(record, dict)
            ]
    report["run_preference"] = only(
        local_json(root, "data/config/run-intent.json"), {"running", "version"}
    )
    report["cloud"] = only(
        local_json(root, "data/cloud/settings.json"),
        {"enabled", "provider", "last_success", "last_file", "monitor_since"},
    )
    report["alerts"] = only(
        local_json(root, "data/config/alerts.json"),
        {"enabled", "backup_max_age_hours", "cloud_max_age_hours"},
    )
    report["image_update"] = only(
        local_json(root, ".image-update.json"),
        {"phase", "state", "status", "error_code", "created", "finished", "desired_running"},
    )
    plugins = root / "data/mods/BepInEx/plugins"
    if plugins.is_dir() and not plugins.is_symlink():
        report["plugins"] = []
        for path in plugins.rglob("*.dll"):
            if len(report["plugins"]) >= 500:
                break
            try:
                if not path.is_symlink() and path.is_file():
                    report["plugins"].append(
                        {"file": path.relative_to(plugins).as_posix(), "bytes": path.stat().st_size}
                    )
            except OSError:
                continue
    service = (
        "valheim-arm-updater-" + hashlib.sha256(str(root).encode()).hexdigest()[:12] + ".service"
    )
    report["updater_service"] = run(
        [
            "systemctl",
            "show",
            service,
            "--property=ActiveState,SubState,Result,ExecMainStatus",
            "--no-pager",
        ]
    )
    if include_logs:
        report["updater_logs"] = run(
            [
                "journalctl",
                "--unit",
                service,
                "--since",
                "1 hour ago",
                "--lines",
                "100",
                "--no-pager",
                "--output=cat",
            ]
        )
    return sanitize(report, secrets_from_env(root))


def write_archive(report, output):
    note = (
        "Valheim ARM diagnostics\nReview report.json before sharing publicly.\n"
        "Only this generated report is included, not saves, credentials, or plugin binaries.\n"
        "Optional logs use best-effort redaction; inspect third-party plugin messages manually.\n"
    )
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream, tarfile.open(fileobj=stream, mode="w:gz") as archive:
        for name, body in (
            ("report.json", json.dumps(report, indent=2, ensure_ascii=False)),
            ("README.txt", note),
        ):
            content = body.encode("utf-8")
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(content), 0o600
            archive.addfile(info, io.BytesIO(content))
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        uid, gid = os.environ.get("SUDO_UID", ""), os.environ.get("SUDO_GID", "")
        if uid.isdigit() and gid.isdigit() and int(uid) > 0:
            os.chown(output, int(uid), int(gid))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path.cwd(), help="Installation directory")
    parser.add_argument("--output", type=Path, help="New .tar.gz report filename")
    parser.add_argument(
        "--include-logs", action="store_true", help="Include bounded, redacted recent logs"
    )
    args = parser.parse_args()
    root = args.directory.resolve()
    if not (root / "compose.yaml").is_file():
        parser.error("Choose the installation directory containing compose.yaml")
    output = args.output or Path.cwd() / (
        "valheim-diagnostics-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".tar.gz"
    )
    try:
        write_archive(collect(root, args.include_logs), output)
    except (OSError, ValueError) as exc:
        parser.exit(
            1,
            "Could not create report: "
            + type(exc).__name__
            + ". Check permissions, free space, and the output filename.\n",
        )
    print("Report created: " + str(output))
    print("Review report.json before attaching it to a GitHub issue. Nothing was uploaded.")


if __name__ == "__main__":
    main()
