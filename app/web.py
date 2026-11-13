import asyncio
import json
import os
import re
import secrets
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .auth import Auth
from .config import Settings
from .metrics import Metrics
from .runtime import Runtime


class Login(BaseModel):
    password: str = Field(min_length=1, max_length=256)


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


class UserList(BaseModel):
    entries: list[str] = Field(max_length=1000)


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
    origin = os.environ.get("PANEL_ORIGIN", "")

    @asynccontextmanager
    async def lifespan(app):
        task = None
        try:
            if lifecycle:
                if not re.fullmatch(r"https://[A-Za-z0-9.\-]+(?::\d+)?", origin):
                    raise RuntimeError("PANEL_ORIGIN HTTPS obrigatório")
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
        return JSONResponse({"detail": [{"loc": e["loc"], "msg": e["msg"]} for e in exc.errors()]}, status_code=422)

    @app.middleware("http")
    async def headers(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    def session(request: Request):
        value = auth.session(request.cookies.get("session"))
        if not value:
            raise HTTPException(401, "Faça login")
        if request.method not in ("GET", "HEAD"):
            if request.headers.get("origin") != origin or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), value["csrf"]):
                raise HTTPException(403, "Origem ou token CSRF inválido")
        return value

    @app.get("/healthz")
    async def health():
        return {"supervisor": "ok", "game": rt.state}

    @app.post("/api/v1/login")
    async def login(body: Login, request: Request, response: Response):
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Origem inválida")
        try:
            token, csrf = await asyncio.to_thread(auth.login, body.password, request.client.host)
        except ValueError as exc:
            raise HTTPException(401, str(exc))
        response.set_cookie("session", token, secure=True, httponly=True, samesite="strict", max_age=8*3600, path="/")
        return {"csrf": csrf}

    @app.get("/api/v1/session")
    async def current_session(value=Depends(session)):
        return {"csrf": value["csrf"]}

    @app.post("/api/v1/logout", dependencies=[Depends(session)])
    async def logout(request: Request, response: Response):
        auth.sessions.pop(request.cookies.get("session"), None)
        response.delete_cookie("session", secure=True, httponly=True, samesite="strict")
        return {"ok": True}

    @app.get("/api/v1/state", dependencies=[Depends(session)])
    async def state():
        build = Path("/opt/build-info.json")
        return rt.status() | {"metrics": list(metrics.history), "image": json.loads(build.read_text()) if build.exists() else {}}

    @app.get("/api/v1/config", dependencies=[Depends(session)])
    async def get_config():
        return rt.config.describe(rt.running)

    @app.patch("/api/v1/config", dependencies=[Depends(session)])
    async def change_config(body: ConfigChange):
        if rt.active:
            raise HTTPException(409, "Aguarde a operação atual antes de alterar configurações")
        try:
            rt.config.change(body.changes, body.reset, body.confirm_world)
        except ValueError as exc:
            raise HTTPException(422, public_error(exc))
        return rt.config.describe(rt.running)

    @app.post("/api/v1/actions", status_code=202, dependencies=[Depends(session)])
    async def action(body: Action):
        if body.action == "restore" and not body.confirm:
            raise HTTPException(422, "Confirmação de restauração obrigatória")
        try:
            return rt.submit(body.action, body.backup)
        except (ValueError, asyncio.QueueFull) as exc:
            raise HTTPException(409, str(exc) or "Fila cheia")

    @app.get("/api/v1/jobs/{job_id}", dependencies=[Depends(session)])
    async def job(job_id: str):
        for item in rt.jobs:
            if item["id"] == job_id:
                return item
        raise HTTPException(404, "Operação não encontrada")

    @app.get("/api/v1/cloud", dependencies=[Depends(session)])
    async def cloud_config():
        return rt.cloud.describe()

    @app.put("/api/v1/cloud", dependencies=[Depends(session)])
    async def cloud_configure(body: CloudChange):
        try:
            return rt.cloud.configure(body.enabled, body.folder, body.config)
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
            raise HTTPException(404, "Lista inválida")
        path = data / "saves" / LISTS[kind]
        return {"entries": path.read_text().splitlines() if path.exists() else []}

    @app.put("/api/v1/users/{kind}", dependencies=[Depends(session)])
    async def put_users(kind: str, body: UserList):
        if kind not in LISTS:
            raise HTTPException(404, "Lista inválida")
        if rt.active:
            raise HTTPException(409, "Aguarde a operação atual antes de editar listas")
        entries = list(dict.fromkeys(e.strip() for e in body.entries if e.strip() and not e.strip().startswith("//")))
        if any(not re.fullmatch(r"(?:[A-Za-z]+_)?[A-Za-z0-9:_-]{1,128}", e) for e in entries):
            raise HTTPException(422, "Use um ID de plataforma por linha, copiado do jogo")
        path = data / "saves" / LISTS[kind]
        fd, temporary = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                file.write("\n".join(entries) + "\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return {"entries": entries}

    @app.get("/api/v1/events", dependencies=[Depends(session)])
    async def events(request: Request):
        async def stream():
            while not await request.is_disconnected():
                if not auth.session(request.cookies.get("session")):
                    return
                payload = rt.status() | {"metric": metrics.history[-1] if metrics.history else None}
                yield "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"
                await asyncio.sleep(5)
        return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})

    static = Path(__file__).parent / "static"
    app.mount("/", StaticFiles(directory=static, html=True), name="panel")
    return app
