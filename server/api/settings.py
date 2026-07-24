from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from config import settings
from middleware.auth import require_role

router = APIRouter(prefix="/api/v2/settings", tags=["settings"])


class SystemSettings(BaseModel):
    app_name: str = settings.APP_NAME
    version: str = settings.VERSION
    debug: bool = settings.DEBUG
    host: str = settings.HOST
    port: int = settings.PORT
    agent_heartbeat_interval: int = settings.AGENT_HEARTBEAT_INTERVAL
    agent_heartbeat_timeout: int = settings.AGENT_HEARTBEAT_TIMEOUT
    cors_origins: list[str] = settings.CORS_ORIGINS
    access_token_expire_minutes: int = settings.ACCESS_TOKEN_EXPIRE_MINUTES
    refresh_token_expire_days: int = settings.REFRESH_TOKEN_EXPIRE_DAYS
    remote_commands_enabled: bool = settings.ENABLE_REMOTE_COMMANDS
    environment: str = settings.ENVIRONMENT


@router.get("/", response_model=SystemSettings)
async def get_settings(_user=Depends(require_role("admin"))):
    return SystemSettings()


class SettingsUpdate(BaseModel):
    debug: bool | None = None
    agent_heartbeat_interval: int | None = None
    agent_heartbeat_timeout: int | None = None
    access_token_expire_minutes: int | None = None


@router.put("/")
async def update_settings(
    body: SettingsUpdate,
    _user=Depends(require_role("admin")),
):
    if body.debug is not None:
        settings.DEBUG = body.debug
    if body.agent_heartbeat_interval is not None:
        settings.AGENT_HEARTBEAT_INTERVAL = body.agent_heartbeat_interval
    if body.agent_heartbeat_timeout is not None:
        settings.AGENT_HEARTBEAT_TIMEOUT = body.agent_heartbeat_timeout
    if body.access_token_expire_minutes is not None:
        settings.ACCESS_TOKEN_EXPIRE_MINUTES = body.access_token_expire_minutes
    return {"status": "updated"}
