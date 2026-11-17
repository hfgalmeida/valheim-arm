import asyncio
import json
import http.client
import os
import re
import secrets
import tempfile
import uuid
from datetime import date, datetime, time as day_time, timedelta
from zoneinfo import ZoneInfo
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import Auth
from .metrics import Metrics
from .mod_configs import ModConfigs, Conflict
from .runtime import Runtime
from .plugins import Plugins, MAX_PLUGIN_SIZE


class Login(BaseModel):
    username: str = Field(default="admin", min_length=1, max_length=32)
    password: str = Field(min_length=1, max_length=256)


class AccountCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=32)
    role: str = Field(pattern=r"^(admin|operator)$")
    password: str = Field(min_length=12, max_length=256)


class AccountChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: str = Field(pattern=r"^(admin|operator)$")
    enabled: bool


class AccountPassword(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=12, max_length=256)


class PasswordChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    current_password: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class ImageUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False
    request_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class ConfigChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    changes: dict = Field(default_factory=dict)
    reset: list[str] = Field(default_factory=list)
    confirm_world: bool = False


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str
    backup: str | None = None
    confirm: bool = False


class PlayerIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=80)
    platform: str = Field(default="", max_length=40)
    confirm: bool = False


class PluginRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(max_length=124)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class PluginDelete(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=512)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool = False


class PluginDeletedRestore(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-f0-9]{32}$")
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")
    confirm: bool = False


class ModConfigChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str = Field(max_length=256)
    content: str = Field(max_length=32768)
    revision: str = Field(pattern=r"^[a-f0-9]{64}$")


class UserList(BaseModel):
    entries: list[str] = Field(max_length=1000)


class AlertChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    url: str = Field(default="", max_length=512)
    backup_max_age_hours: int | None = Field(default=None, ge=0, le=720, strict=True)
    cloud_max_age_hours: int | None = Field(default=None, ge=0, le=720, strict=True)


class CloudChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    folder: str = Field(min_length=1, max_length=256)
    config: str = Field(default="", max_length=65536)


LISTS = {"admins": "adminlist.txt", "banned": "bannedlist.txt", "permitted": "permittedlist.txt"}


def public_error(exc):
    if isinstance(exc, ValidationError):
        return "; ".join(".".join(map(str, e["loc"])) + ": " + e["msg"] for e in exc.errors())
    return str(exc)


def create_app(data=None, runtime=None, lifecycle=True):
    data = data or Path(os.environ.get("DATA_DIR", "/data"))
    rt = runtime or Runtime(data)
    auth, metrics = Auth(data), Metrics(data)
    rt.account_authorizer = lambda job: auth.accounts.allowed_actor(
        job["actor"] | {"credential": job.get("actor_credential")}
    )
    origin = os.environ.get("PANEL_ORIGIN", "")
    mod_configs = ModConfigs(data)
    plugins = Plugins(data)

    @asynccontextmanager
    async def lifespan(app):
        task = None
        try:
            if lifecycle:
                if not re.fullmatch(r"https://[A-Za-z0-9.\-]+(?::\d+)?", origin):
                    raise RuntimeError("HTTPS PANEL_ORIGIN is required")
                await rt.boot()
                task = asyncio.create_task(metrics.run())
            yield
        finally:
            if task:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if lifecycle:
                await rt.shutdown()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runtime, app.state.auth, app.state.metrics = rt, auth, metrics

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        # Pydantic's default error response can echo secret input.
        return JSONResponse(
            {"detail": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]}, status_code=422
        )

    def operation(request):
        path = request.url.path
        names = {
            "actions": "server-action",
            "config": "configuration",
            "accounts": "accounts",
            "password": "password-change",
            "plugins": "plugins",
            "mod-config": "plugin-config",
            "users": "player-lists",
            "player-directory": "player-directory",
            "alerts": "alerts",
            "cloud": "cloud",
            "image-update": "image-update",
            "backups": "backup-download",
            "logout": "logout",
        }
        part = path.split("/")[3] if len(path.split("/")) > 3 else "access"
        return names.get(part, "access")

    @app.middleware("http")
    async def headers(request, call_next):
        request.state.audit_request_id = uuid.uuid4().hex
        response = await call_next(request)
        if getattr(request.state, "audit_started", False):
            try:
                rt.audit.append(
                    request.state.audit_actor,
                    operation(request),
                    (
                        "enfileirado"
                        if response.status_code == 202
                        else "completed" if response.status_code < 400 else "failed"
                    ),
                    getattr(
                        request.state,
                        "audit_resource",
                        request.scope.get("route").path if request.scope.get("route") else "panel",
                    ),
                    request.state.audit_request_id,
                    getattr(request.state, "audit_job_id", None),
                    getattr(request.state, "audit_changes", None),
                )
            except OSError:
                response.headers["X-Audit-Warning"] = "unavailable"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def session(request: Request):
        value = auth.session(request.cookies.get("session"))
        if not value:
            raise HTTPException(401, "Sign in")
        user = value["user"]
        path = request.url.path
        if request.method not in ("GET", "HEAD"):
            if request.headers.get("origin") != origin or not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), value["csrf"]
            ):
                raise HTTPException(403, "Invalid origin or CSRF token")
        if user["must_change_password"] and path not in (
            "/api/v1/session",
            "/api/v1/password",
            "/api/v1/logout",
        ):
            raise HTTPException(403, "Change the temporary password before accessing the panel")
        allowed = (
            request.method == "GET"
            and (
                path in ("/api/v1/state", "/api/v1/session", "/api/v1/events")
                or path.startswith("/api/v1/jobs/")
            )
        ) or (
            request.method == "POST"
            and path in ("/api/v1/actions", "/api/v1/logout", "/api/v1/password")
        )
        if user["role"] != "admin" and not allowed:
            try:
                rt.audit.append(
                    user,
                    operation(request),
                    "denied",
                    "recurso administrativo",
                    request.state.audit_request_id,
                )
            except OSError:
                pass
            raise HTTPException(403, "Administrator access required")
        if request.method not in ("GET", "HEAD"):
            if path not in ("/api/v1/logout", "/api/v1/image-update", "/api/v1/password"):
                rt.host_updates.status()
                if rt.host_updates.busy:
                    raise HTTPException(
                        409,
                        "Image update in progress; wait before performing other operations.",
                    )
        if (
            request.method not in ("GET", "HEAD") or path.startswith("/api/v1/backups/")
        ) and not getattr(request.state, "audit_started", False):
            try:
                rt.audit.append(
                    user, operation(request), "requested", "panel", request.state.audit_request_id
                )
            except OSError:
                raise HTTPException(
                    503,
                    "Audit unavailable; check disk space and permissions before changing the server",
                ) from None
            request.state.audit_actor = user
            request.state.audit_started = True
        return value

    def public_job(item, user):
        if user["role"] == "admin":
            return {k: v for k, v in item.items() if k != "actor_credential"}
        result = {
            k: item[k]
            for k in (
                "id",
                "action",
                "state",
                "created",
                "started",
                "finished",
                "duration",
                "attempts",
                "origin",
            )
            if k in item
        }
        if item.get("error"):
            result["error"] = (
                "Waiting for an empty, ready server"
                if item["state"] == "deferred"
                else "Operation did not complete; contact the administrator"
            )
        if item.get("result"):
            result["result"] = (
                "Operation completed" if item["state"] == "completed" else "Request closed"
            )
        return result

    def state_for(value):
        status = rt.status()
        user = value["user"]
        if user["role"] == "admin":
            status["jobs"] = [public_job(j, user) for j in status["jobs"]]
            return status
        detail = {
            "ready": "Server ready for connections.",
            "starting": "The game is starting.",
            "stopping": "Waiting for save and shutdown.",
            "stopped": "Game stopped.",
            "recovering": "Automatic recovery in progress.",
            "failed": "Game failure; contact the administrator.",
        }
        diagnostic = status["diagnostics"]
        return {
            **{k: status[k] for k in ("state", "running", "players", "crossplay", "join_code")},
            "timezone": rt.config.effective().timezone,
            "server_name": rt.config.effective().server_name,
            "diagnostics": {
                "phase": diagnostic["phase"],
                "detail": detail.get(diagnostic["phase"], "Contact the administrator."),
                "retry_in_seconds": diagnostic["retry_in_seconds"],
                "last_failure": None,
            },
            "logs": [],
            "jobs": [
                public_job(j, user) for j in rt.jobs if j.get("actor", {}).get("id") == user["id"]
            ],
        }

    @app.get("/healthz")
    async def health():
        return {
            "supervisor": "ok",
            "game": rt.state,
            "desired_running": rt.run_intent.running,
            "run_intent_error": bool(rt.run_intent.error),
        }

    @app.post("/api/v1/login")
    async def login(body: Login, request: Request, response: Response):
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Invalid origin")
        try:
            token, csrf = await asyncio.to_thread(
                auth.login, body.password, request.client.host, body.username
            )
        except ValueError as exc:
            raise HTTPException(401, str(exc))
        try:
            rt.audit.append(
                auth.sessions[token]["user"],
                "login",
                "completed",
                "panel",
                request.state.audit_request_id,
            )
        except OSError:
            auth.sessions.pop(token, None)
            raise HTTPException(503, "Audit unavailable; contact the administrator") from None
        response.set_cookie(
            "session",
            token,
            secure=True,
            httponly=True,
            samesite="strict",
            max_age=8 * 3600,
            path="/",
        )
        return {"csrf": csrf, "user": auth.sessions[token]["user"]}

    @app.get("/api/v1/session")
    async def current_session(value=Depends(session)):
        return {"csrf": value["csrf"], "user": value["user"]}

    @app.post("/api/v1/logout", dependencies=[Depends(session)])
    async def logout(request: Request, response: Response):
        auth.sessions.pop(request.cookies.get("session"), None)
        response.delete_cookie("session", secure=True, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/v1/state")
    async def state(value=Depends(session)):
        result = state_for(value) | {"metrics": list(metrics.history)}
        build = Path("/opt/build-info.json")
        if value["user"]["role"] == "admin":
            result["image"] = json.loads(build.read_text()) if build.exists() else {}
        return result

    @app.get("/api/v1/config", dependencies=[Depends(session)])
    async def get_config():
        return rt.config.describe(rt.running)

    @app.patch("/api/v1/config", dependencies=[Depends(session)])
    async def change_config(body: ConfigChange, request: Request):
        if rt.active:
            raise HTTPException(409, "Wait for the current operation before changing settings")
        try:
            before = rt.config.effective().model_dump()
            after = rt.config.change(body.changes, body.reset, body.confirm_world).model_dump()
            request.state.audit_changes = {
                key: (
                    {"changed": True}
                    if any(secret in key for secret in ("password", "token", "url", "secret"))
                    else {"before": before[key], "after": after[key]}
                )
                for key in before
                if before[key] != after[key]
            }
        except ValueError as exc:
            raise HTTPException(422, public_error(exc))
        return rt.config.describe(rt.running)

    @app.post("/api/v1/actions", status_code=202)
    async def action(body: Action, request: Request, value=Depends(session)):
        operator = value["user"]["role"] == "operator"
        if operator and body.action not in ("start", "stop", "restart"):
            raise HTTPException(403, "Operators can only start, stop, and restart the server")
        if body.action == "restore" and not body.confirm:
            raise HTTPException(422, "Restore confirmation required")
        try:
            item = rt.submit(
                body.action,
                body.backup,
                actor=value["user"],
                require_idle=operator and body.action in ("stop", "restart"),
                request_id=request.state.audit_request_id,
                credential=value["credential"],
            )
            request.state.audit_job_id = item["id"]
            return public_job(item, value["user"])
        except (ValueError, asyncio.QueueFull) as exc:
            raise HTTPException(409, str(exc) or "Queue full")

    @app.get("/api/v1/image-version", dependencies=[Depends(session)])
    async def check_image_version():
        return await asyncio.to_thread(rt.image_version.check)

    @app.get("/api/v1/image-update", dependencies=[Depends(session)])
    async def image_update_status():
        return await asyncio.to_thread(rt.host_updates.status)

    @app.post("/api/v1/image-update", status_code=202, dependencies=[Depends(session)])
    async def request_image_update(
        body: ImageUpdateRequest, request: Request, value=Depends(session)
    ):
        if not body.confirm:
            raise HTTPException(422, "Confirm the image update and possible player disconnections.")
        if rt.active or not rt.queue.empty() or rt.closing:
            raise HTTPException(
                409, "Wait for game operations to finish before updating the image."
            )
        request.state.audit_job_id = body.request_id
        try:
            result = await asyncio.to_thread(rt.host_updates.start, body.request_id)
            return result
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        except (OSError, http.client.HTTPException):
            raise HTTPException(503, "Host updater service unavailable.") from None

    @app.get("/api/v1/jobs/{job_id}")
    async def job(job_id: str, value=Depends(session)):
        for item in rt.jobs:
            if item["id"] == job_id and (
                value["user"]["role"] == "admin"
                or item.get("actor", {}).get("id") == value["user"]["id"]
            ):
                return public_job(item, value["user"])
        raise HTTPException(404, "Operation not found")

    def config_error(exc):
        code = (
            409 if isinstance(exc, Conflict) else 404 if isinstance(exc, FileNotFoundError) else 422
        )
        raise HTTPException(
            code,
            (
                str(exc)
                if isinstance(exc, ValueError)
                else "File unavailable; check permissions and free space"
            ),
        )

    def plugin_stopped():
        if (
            rt.live
            or rt.active
            or not rt.queue.empty()
            or any(j["state"] in ("queued", "running", "deferred") for j in rt.jobs)
        ):
            raise HTTPException(
                409, "Stop the game and wait for pending operations before changing plugins"
            )

    @app.get("/api/v1/plugins", dependencies=[Depends(session)])
    async def list_plugins():
        try:
            files = await asyncio.to_thread(plugins.list)
            for file in files:
                file["session_state"] = rt.mod_diagnostics.file_state(
                    plugins.root / file["name"], rt.live
                )
            return {
                "files": files,
                "deleted": await asyncio.to_thread(plugins.list_deleted),
                "diagnostics": rt.mod_diagnostics.describe(rt.live, rt.config.effective().bepinex),
            }
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.post("/api/v1/plugins", dependencies=[Depends(session)])
    async def upload_plugin(request: Request, name: str, revision: str | None = None):
        plugin_stopped()
        if revision is not None and not re.fullmatch(r"[a-f0-9]{64}", revision):
            raise HTTPException(422, "Invalid replacement confirmation")
        if (
            request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            != "application/octet-stream"
        ):
            raise HTTPException(415, "Send the binary DLL content")
        try:
            with plugins.staging(name) as (file, temporary):
                size = 0
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_PLUGIN_SIZE:
                        raise HTTPException(413, "The DLL must not exceed 16 MiB")
                    file.write(chunk)
                file.flush()
                os.fsync(file.fileno())
                file.close()
                # State and authentication may have changed during the transfer.
                session(request)
                plugin_stopped()
                result = plugins.commit(name, temporary, revision)
                request.state.audit_resource = "Plugin " + result["name"]
                request.state.audit_changes = {
                    "revision": {"before": revision, "after": result["revision"]}
                }
                return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.post("/api/v1/plugins/restore", dependencies=[Depends(session)])
    async def restore_plugin(body: PluginRestore, request: Request):
        plugin_stopped()
        try:
            result = plugins.restore(body.name, body.revision)
            request.state.audit_resource = "Plugin " + result["name"]
            request.state.audit_changes = {
                "revision": {"before": body.revision, "after": result["revision"]}
            }
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.post("/api/v1/plugins/delete", dependencies=[Depends(session)])
    async def delete_plugin(body: PluginDelete, request: Request):
        if not body.confirm:
            raise HTTPException(422, "Confirm plugin deletion")
        plugin_stopped()
        try:
            result = plugins.delete(body.name, body.revision)
            request.state.audit_resource = "Plugin " + body.name
            request.state.audit_changes = {
                "file": {"before": "instalado", "after": "recoverable deletion"},
                "revision": body.revision,
                "copy": result["id"],
            }
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.post("/api/v1/plugins/restore-deleted", dependencies=[Depends(session)])
    async def restore_deleted_plugin(body: PluginDeletedRestore, request: Request):
        if not body.confirm:
            raise HTTPException(422, "Confirm restoration of the deleted plugin")
        plugin_stopped()
        try:
            result = plugins.restore_deleted(body.id, body.revision)
            request.state.audit_resource = "Plugin " + result["name"]
            request.state.audit_changes = {
                "file": {"before": "deleted", "after": "instalado"},
                "revision": result["revision"],
                "copy": body.id,
            }
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.get("/api/v1/mod-configs", dependencies=[Depends(session)])
    async def list_mod_configs():
        try:
            return {"files": mod_configs.list()}
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.get("/api/v1/mod-config", dependencies=[Depends(session)])
    async def read_mod_config(path: str, previous: bool = False):
        try:
            return mod_configs.read(path, previous)
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.put("/api/v1/mod-config", dependencies=[Depends(session)])
    async def save_mod_config(body: ModConfigChange, request: Request):
        if rt.live or rt.active or not rt.queue.empty():
            raise HTTPException(
                409,
                "Stop the game in the panel and wait for pending operations before editing plugins",
            )
        try:
            result = mod_configs.save(body.path, body.content, body.revision)
            request.state.audit_resource = "Plugin configuration " + result["path"]
            request.state.audit_changes = {
                "file": {"changed": True},
                "revision": {"before": body.revision, "after": result["revision"]},
            }
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.get("/api/v1/alerts", dependencies=[Depends(session)])
    async def alert_config():
        try:
            await rt.alerts.check_freshness(rt)
        except (OSError, ValueError) as exc:
            config_error(exc)
        return rt.alerts.describe()

    @app.put("/api/v1/alerts", dependencies=[Depends(session)])
    async def alert_configure(body: AlertChange, request: Request):
        try:
            before = rt.alerts.describe()
            rt.alerts.configure(
                body.enabled, body.url, body.backup_max_age_hours, body.cloud_max_age_hours
            )
            await rt.alerts.check_freshness(rt)
            result = rt.alerts.describe()
            request.state.audit_changes = {
                "enabled": {"before": before["enabled"], "after": result["enabled"]}
            }
            for key in ("backup_max_age_hours", "cloud_max_age_hours"):
                if before[key] != result[key]:
                    request.state.audit_changes[key] = {"before": before[key], "after": result[key]}
            if body.url:
                request.state.audit_changes["webhook"] = {"changed": True}
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.post("/api/v1/alerts/test", dependencies=[Depends(session)])
    async def alert_test():
        try:
            return await rt.alerts.test()
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/v1/cloud", dependencies=[Depends(session)])
    async def cloud_config():
        return rt.cloud.describe()

    @app.put("/api/v1/cloud", dependencies=[Depends(session)])
    async def cloud_configure(body: CloudChange, request: Request):
        try:
            before = rt.cloud.describe()
            result = rt.cloud.configure(body.enabled, body.folder, body.config)
            request.state.audit_changes = {
                key: {"before": before[key], "after": result[key]}
                for key in ("enabled", "folder", "provider")
                if before[key] != result[key]
            }
            if body.config:
                request.state.audit_changes["connection"] = {"changed": True}
            return result
        except ValueError as exc:
            raise HTTPException(422, str(exc))

    @app.get("/api/v1/backups", dependencies=[Depends(session)])
    async def backups():
        return rt.backups.list()

    @app.get("/api/v1/backups/{name}", dependencies=[Depends(session)])
    async def download_backup(name: str):
        try:
            path = rt.backups.resolve(name)
        except ValueError as exc:
            raise HTTPException(404, str(exc))
        return FileResponse(path, filename=path.name, media_type="application/gzip")

    @app.get("/api/v1/users/{kind}", dependencies=[Depends(session)])
    async def get_users(kind: str):
        if kind not in LISTS:
            raise HTTPException(404, "Invalid list")
        path = data / "saves" / LISTS[kind]
        known = {entry["id"]: entry for entry in rt.player_directory.describe()}
        for filename in LISTS.values():
            existing = data / "saves" / filename
            for entry in existing.read_text().splitlines() if existing.exists() else []:
                entry = entry.strip()
                if re.fullmatch(r"(?:[A-Za-z]+_)?[A-Za-z0-9:_-]{1,128}", entry):
                    known.setdefault(
                        entry,
                        {
                            "id": entry,
                            "last_seen": None,
                            "name": "",
                            "source": "list",
                            "confirmed": True,
                            "platform": entry.split("_", 1)[0] if "_" in entry else None,
                        },
                    )
        return {
            "entries": path.read_text().splitlines() if path.exists() else [],
            "known_players": sorted(known.values(), key=lambda entry: entry["id"]),
        }

    @app.put("/api/v1/player-directory", dependencies=[Depends(session)])
    async def register_player(body: PlayerIdentity, request: Request):
        if not body.confirm:
            raise HTTPException(422, "Confirm that the ID was checked in the in-game F2 window")
        try:
            previous = {entry["id"]: entry for entry in rt.player_directory.describe()}
            result = rt.player_directory.register(body.id, body.name, body.platform)
            request.state.audit_resource = "Player identity " + result["id"]
            request.state.audit_changes = {
                "name": {
                    "before": previous.get(result["id"], {}).get("name", ""),
                    "after": result["name"],
                },
                "source": "administrator confirmation",
                "platform": {
                    "before": previous.get(result["id"], {}).get("platform"),
                    "after": result["platform"],
                },
            }
            return result
        except (OSError, ValueError) as exc:
            config_error(exc)

    @app.put("/api/v1/users/{kind}", dependencies=[Depends(session)])
    async def put_users(kind: str, body: UserList, request: Request):
        if kind not in LISTS:
            raise HTTPException(404, "Invalid list")
        if rt.active:
            raise HTTPException(409, "Wait for the current operation before editing lists")
        entries = list(
            dict.fromkeys(
                e.strip() for e in body.entries if e.strip() and not e.strip().startswith("//")
            )
        )
        if any(not re.fullmatch(r"(?:[A-Za-z]+_)?[A-Za-z0-9:_-]{1,128}", e) for e in entries):
            raise HTTPException(422, "Use one platform ID per line, copied from the game")
        path = data / "saves" / LISTS[kind]
        previous = path.read_text().splitlines() if path.exists() else []
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write("\n".join(entries) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        request.state.audit_resource = "Lista " + kind
        request.state.audit_changes = {"entries": {"before": previous, "after": entries}}
        return {"entries": entries}

    @app.get("/api/v1/events", dependencies=[Depends(session)])
    async def events(request: Request):
        async def stream():
            while not await request.is_disconnected():
                value = auth.session(request.cookies.get("session"))
                if not value or value["user"]["must_change_password"]:
                    return
                payload = state_for(value) | {
                    "metric": metrics.history[-1] if metrics.history else None
                }
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(5)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"}
        )

    @app.post("/api/v1/password")
    async def change_password(
        body: PasswordChange, request: Request, response: Response, value=Depends(session)
    ):
        user = auth.accounts.get(value["user"]["id"])
        try:
            valid = await asyncio.to_thread(
                auth.verify_current, user["id"], body.current_password, value["credential"]
            )
        except ValueError as exc:
            raise HTTPException(429, str(exc)) from None
        if not valid:
            raise HTTPException(403, "Invalid current password")
        if secrets.compare_digest(body.current_password, body.password):
            raise HTTPException(422, "Choose a password different from the current password")
        try:
            await asyncio.to_thread(
                auth.accounts.password, user["id"], body.password, False, value["credential"]
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        request.state.audit_changes = {"password": {"changed": True}}
        response.delete_cookie("session", secure=True, httponly=True, samesite="strict", path="/")
        return {"ok": True, "login_required": True}

    @app.get("/api/v1/accounts", dependencies=[Depends(session)])
    async def accounts():
        return {"users": [auth.accounts.public(u) for u in auth.accounts.all()]}

    @app.post("/api/v1/accounts", status_code=201, dependencies=[Depends(session)])
    async def create_account(body: AccountCreate, request: Request):
        try:
            result = await asyncio.to_thread(
                auth.accounts.create, body.username, body.role, body.password
            )
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        request.state.audit_changes = {"account": {"after": result}}
        return result

    @app.patch("/api/v1/accounts/{user_id}", dependencies=[Depends(session)])
    async def edit_account(user_id: str, body: AccountChange, request: Request):
        old = auth.accounts.get(user_id)
        try:
            result = auth.accounts.change(user_id, body.role, body.enabled)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        request.state.audit_changes = {
            "account": {"before": auth.accounts.public(old), "after": result}
        }
        for pending in rt.jobs:
            if pending.get("actor", {}).get("id") == user_id and pending["state"] in (
                "queued",
                "deferred",
            ):
                pending["state"] = "cancelled"
        rt.persist_pending()
        return result

    @app.post("/api/v1/accounts/{user_id}/password", dependencies=[Depends(session)])
    async def reset_account_password(user_id: str, body: AccountPassword, request: Request):
        target = auth.accounts.get(user_id)
        if target:
            request.state.audit_resource = "Conta " + target["username"]
        try:
            await asyncio.to_thread(auth.accounts.password, user_id, body.password, True)
        except ValueError as exc:
            raise HTTPException(404, str(exc)) from None
        request.state.audit_changes = {"password": {"changed": True}}
        for pending in rt.jobs:
            if pending.get("actor", {}).get("id") == user_id and pending["state"] in (
                "queued",
                "deferred",
            ):
                pending["state"] = "cancelled"
        rt.persist_pending()
        return {"ok": True}

    @app.get("/api/v1/audit", dependencies=[Depends(session)])
    async def audit(
        user: str | None = Query(None, max_length=32),
        operation: str | None = Query(None, max_length=64),
        since: date | None = None,
        until: date | None = None,
        limit: int = Query(100, ge=1, le=200),
    ):
        zone = ZoneInfo(rt.config.effective().timezone)
        start = datetime.combine(since, day_time.min, zone).timestamp() if since else None
        end = (
            datetime.combine(until + timedelta(days=1), day_time.min, zone).timestamp()
            if until
            else None
        )
        return {
            "events": rt.audit.query(
                user, operation, start, end - 0.000001 if end else None, limit
            ),
            "error": rt.audit.error,
            "retention_days": 90,
            "limit": 2000,
        }

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="panel")
    return app
