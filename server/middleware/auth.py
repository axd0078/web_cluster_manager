from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.permissions import has_permissions, normalize_role, permissions_for
from core.security import decode_token
from database import get_db
from models.user import User

security_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    token = request.cookies.get(settings.ACCESS_COOKIE_NAME)
    if credentials is not None:
        token = credentials.credentials
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="未提供认证令牌")
    try:
        payload = decode_token(token, expected_type="access")
        user_id: str | None = payload.get("sub")
        if user_id is None or not payload.get("sid"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="令牌验证失败",
        ) from exc

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None or user.disabled:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if int(payload.get("ver", -1)) != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效")
    user.role = normalize_role(user.role)
    request.state.auth_payload = payload
    return user


def step_up_payload(request: Request, user: User) -> dict | None:
    token = request.cookies.get(settings.STEP_UP_COOKIE_NAME)
    access = getattr(request.state, "auth_payload", None)
    if not token or not isinstance(access, dict):
        return None
    try:
        payload = decode_token(token, expected_type="step_up")
    except ValueError:
        return None
    if (
        payload.get("sub") != user.id
        or payload.get("sid") != access.get("sid")
        or int(payload.get("ver", -1)) != user.token_version
    ):
        return None
    return payload


def has_step_up(request: Request, user: User) -> bool:
    return step_up_payload(request, user) is not None


def require_permission(permission: str):
    async def checker(
        request: Request,
        user: User = Depends(get_current_user),
    ) -> User:
        if has_permissions(user.role, permission):
            return user
        if has_permissions(user.role, permission, step_up=has_step_up(request, user)):
            return user
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="此操作需要管理员二次认证" if user.role == "admin" else "权限不足",
        )

    return checker


def permission_snapshot(request: Request, user: User) -> tuple[list[str], str | None]:
    payload = step_up_payload(request, user)
    expires_at = None
    if payload is not None:
        expires_at = datetime.fromtimestamp(float(payload["exp"]), tz=timezone.utc).isoformat()
    return sorted(permissions_for(user.role, step_up=payload is not None)), expires_at
