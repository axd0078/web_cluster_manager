from __future__ import annotations

import asyncio
import contextlib
import hmac
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from starlette.exceptions import HTTPException as StarletteHTTPException

from api import (
    agents as agents_router,
    alerts as alerts_router,
    audit as audit_router,
    auth as auth_router,
    containers as containers_router,
    files as files_router,
    monitor as monitor_router,
    nodes as nodes_router,
    settings as settings_router,
    tasks as tasks_router,
    terminal as terminal_router,
    updates as updates_router,
    ws as ws_router,
)
from config import JWT_SECRET_CONFIGURED, settings
from core.security import hash_password, verify_password
from database import async_session, engine, init_db
from models.user import User
from services.node_service import NodeService
from services.file_transfer_service import file_transfer_service


def _validate_production_settings() -> None:
    if settings.ENVIRONMENT.lower() != "production":
        return
    problems: list[str] = []
    if settings.DEBUG:
        problems.append("WCM_DEBUG must be false")
    if not settings.COOKIE_SECURE:
        problems.append("WCM_COOKIE_SECURE must be true")
    if not JWT_SECRET_CONFIGURED:
        problems.append("WCM_JWT_SECRET or WCM_JWT_SECRET_FILE is required")
    if any(not origin.startswith("https://") for origin in settings.CORS_ORIGINS):
        problems.append("all production CORS origins must use https")
    if problems:
        raise RuntimeError("Unsafe production configuration: " + "; ".join(problems))


async def _bootstrap_admin() -> None:
    async with async_session() as db:
        count = await db.scalar(select(func.count(User.id)))
        if count:
            if settings.ENVIRONMENT.lower() == "production":
                result = await db.execute(select(User).where(User.username == "admin"))
                admin = result.scalar_one_or_none()
                if admin and verify_password("admin123", admin.password):
                    raise RuntimeError("Refusing production startup: the legacy admin/admin123 password is still active")
            return
        password = settings.BOOTSTRAP_ADMIN_PASSWORD
        if len(password) < 12:
            raise RuntimeError(
                "No users exist. Set WCM_BOOTSTRAP_ADMIN_PASSWORD_FILE "
                "or a 12+ character WCM_BOOTSTRAP_ADMIN_PASSWORD for first startup."
            )
        db.add(User(
            username=settings.BOOTSTRAP_ADMIN_USERNAME,
            password=hash_password(password), role="admin",
        ))
        await db.commit()


async def _stale_node_loop() -> None:
    while True:
        await asyncio.sleep(max(10, settings.AGENT_HEARTBEAT_INTERVAL))
        async with async_session() as db:
            service = NodeService(db)
            await service.mark_offline_stale(settings.AGENT_HEARTBEAT_TIMEOUT)
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _validate_production_settings()
    Path(settings.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.UPDATES_DIR).mkdir(parents=True, exist_ok=True)
    Path(settings.LOGS_DIR).mkdir(parents=True, exist_ok=True)
    await init_db()
    await _bootstrap_admin()
    await file_transfer_service.recover_stale()
    stale_task = asyncio.create_task(_stale_node_loop())
    cleanup_task = asyncio.create_task(file_transfer_service.cleanup_loop())
    app.state.startup_complete = True
    yield
    for task in (stale_task, cleanup_task):
        task.cancel()
    for task in (stale_task, cleanup_task):
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await engine.dispose()


app = FastAPI(title=settings.APP_NAME, version=settings.VERSION, lifespan=lifespan)

app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.TRUSTED_HOSTS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
)


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.cookies.get(settings.ACCESS_COOKIE_NAME) and not request.headers.get("authorization"):
            cookie = request.cookies.get(settings.CSRF_COOKIE_NAME, "")
            header = request.headers.get("x-csrf-token", "")
            if not cookie or not header or not hmac.compare_digest(cookie, header):
                return JSONResponse(status_code=403, content={"detail": "CSRF 校验失败"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; connect-src 'self' ws: wss:"
    )
    if settings.COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


for router in (
    auth_router.router, agents_router.router, nodes_router.router,
    containers_router.router, ws_router.router, tasks_router.router,
    files_router.router, updates_router.router, monitor_router.router,
    alerts_router.router, audit_router.router, settings_router.router,
    terminal_router.router,
):
    app.include_router(router)


@app.get("/api/v2/health")
async def health_check():
    return {"status": "ok", "version": settings.VERSION}


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            # Never turn an unknown API/WebSocket endpoint into the SPA HTML.
            # Clients must receive a real 404 instead of parsing index.html as JSON.
            request_path = str(scope.get("path") or path).lstrip("/")
            if request_path.startswith(("api/", "ws/")):
                raise
            if exc.status_code == 404 and "." not in Path(path).name:
                return await super().get_response("index.html", scope)
            raise


static_dir = Path(__file__).parent.parent / "web-ui" / "dist"
if static_dir.exists():
    app.mount("/", SPAStaticFiles(directory=str(static_dir), html=True), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host=settings.HOST, port=settings.PORT, reload=settings.DEBUG)
