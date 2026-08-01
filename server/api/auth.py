from __future__ import annotations

import time
from collections import defaultdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.connection_manager import manager
from core.permissions import normalize_role, permissions_for
from core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    create_refresh_token,
    create_step_up_token,
    decode_token,
    generate_csrf_token,
    generate_session_id,
    hash_password,
    verify_password,
)
from database import get_db
from middleware.auth import (
    get_current_user,
    permission_snapshot,
    require_permission,
)
from models.node import AuditLog
from models.user import User
from schemas.auth import (
    LoginRequest,
    PasswordChange,
    StepUpRequest,
    StepUpResponse,
    UserCreate,
    UserResponse,
    UserUpdate,
)

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])
_failed_logins: dict[str, list[float]] = defaultdict(list)
_failed_step_up: dict[str, list[float]] = defaultdict(list)


def _cookie_common() -> dict:
    return {
        "secure": settings.COOKIE_SECURE,
        "samesite": "strict",
        "domain": settings.COOKIE_DOMAIN,
    }


def _set_auth_cookies(response: Response, user: User, *, sid: str) -> None:
    response.set_cookie(
        settings.ACCESS_COOKIE_NAME,
        create_access_token(user.id, user.role, user.token_version, sid),
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        path="/",
        **_cookie_common(),
    )
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME,
        create_refresh_token(user.id, user.token_version, sid),
        httponly=True,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v2/auth",
        **_cookie_common(),
    )
    response.set_cookie(
        settings.CSRF_COOKIE_NAME,
        generate_csrf_token(),
        httponly=False,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/",
        **_cookie_common(),
    )


def _clear_step_up_cookie(response: Response) -> None:
    response.delete_cookie(
        settings.STEP_UP_COOKIE_NAME,
        path="/",
        domain=settings.COOKIE_DOMAIN,
    )


def _clear_auth_cookies(response: Response) -> None:
    for name, path in (
        (settings.ACCESS_COOKIE_NAME, "/"),
        (settings.REFRESH_COOKIE_NAME, "/api/v2/auth"),
        (settings.CSRF_COOKIE_NAME, "/"),
        (settings.STEP_UP_COOKIE_NAME, "/"),
    ):
        response.delete_cookie(name, path=path, domain=settings.COOKIE_DOMAIN)


def _check_rate_limit(
    bucket: dict[str, list[float]],
    key: str,
    *,
    attempts: int,
    window: int,
) -> None:
    now = time.monotonic()
    cutoff = now - window
    if key not in bucket and len(bucket) >= 10_000:
        stale_keys = [
            item_key
            for item_key, timestamps in bucket.items()
            if not timestamps or max(timestamps) < cutoff
        ]
        for item_key in stale_keys:
            bucket.pop(item_key, None)
        if len(bucket) >= 10_000:
            oldest_key = min(
                bucket,
                key=lambda item_key: max(bucket[item_key], default=0.0),
            )
            bucket.pop(oldest_key, None)
    values = [value for value in bucket.get(key, []) if value >= cutoff]
    bucket[key] = values
    if len(values) >= attempts:
        raise HTTPException(status_code=429, detail="认证尝试过多，请稍后重试")


def _response(user: User, request: Request | None = None) -> UserResponse:
    permissions: list[str] = []
    step_up_expires_at = None
    if request is not None:
        permissions, step_up_expires_at = permission_snapshot(request, user)
    else:
        permissions = sorted(permissions_for(user.role))
    return UserResponse(
        id=user.id,
        username=user.username,
        role=normalize_role(user.role),
        disabled=bool(user.disabled),
        permissions=permissions,
        step_up_expires_at=step_up_expires_at,
    )


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
):
    client = request.client.host if request.client else "unknown"
    _check_rate_limit(
        _failed_logins,
        client,
        attempts=settings.LOGIN_RATE_LIMIT_ATTEMPTS,
        window=settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS,
    )
    user = await db.scalar(select(User).where(User.username == body.username))
    password_hash = user.password if user is not None else DUMMY_PASSWORD_HASH
    valid = verify_password(body.password, password_hash)
    if user is None or not valid or user.disabled:
        _failed_logins[client].append(time.monotonic())
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _failed_logins.pop(client, None)
    sid = generate_session_id()
    _clear_step_up_cookie(response)
    _set_auth_cookies(response, user, sid=sid)
    db.add(AuditLog(user_id=user.id, action="auth.login", resource=user.id, ip=client))
    return _response(user)


@router.post("/refresh", response_model=UserResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="缺少刷新令牌")
    try:
        payload = decode_token(token, expected_type="refresh")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="令牌无效") from exc
    sid = payload.get("sid")
    if not isinstance(sid, str) or not sid:
        raise HTTPException(status_code=401, detail="会话已失效")
    user = await db.scalar(select(User).where(User.id == payload.get("sub")))
    if (
        user is None
        or user.disabled
        or int(payload.get("ver", -1)) != user.token_version
    ):
        raise HTTPException(status_code=401, detail="会话已失效")
    _set_auth_cookies(response, user, sid=sid)
    return _response(user)


@router.get("/me", response_model=UserResponse)
async def me(request: Request, user: User = Depends(get_current_user)):
    return _response(user, request)


@router.post("/step-up", response_model=StepUpResponse)
async def step_up(
    body: StepUpRequest,
    request: Request,
    response: Response,
    user: User = Depends(get_current_user),
):
    client = request.client.host if request.client else "unknown"
    key = f"{user.id}:{client}"
    _check_rate_limit(
        _failed_step_up,
        key,
        attempts=settings.STEP_UP_RATE_LIMIT_ATTEMPTS,
        window=settings.STEP_UP_RATE_LIMIT_WINDOW_SECONDS,
    )
    # Run the same password check for every authenticated role, then fail with
    # one generic response. This avoids a role/password oracle.
    valid = verify_password(body.password, user.password)
    if not valid or user.role != "admin":
        _failed_step_up[key].append(time.monotonic())
        raise HTTPException(status_code=403, detail="二次认证失败")
    _failed_step_up.pop(key, None)
    access = request.state.auth_payload
    token, expires_at = create_step_up_token(
        user.id,
        str(access["sid"]),
        user.token_version,
    )
    response.set_cookie(
        settings.STEP_UP_COOKIE_NAME,
        token,
        httponly=True,
        max_age=settings.STEP_UP_EXPIRE_MINUTES * 60,
        path="/",
        **_cookie_common(),
    )
    return StepUpResponse(expires_at=expires_at.isoformat())


@router.delete("/step-up", status_code=204)
async def revoke_step_up(
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    from models.terminal import TerminalTicket
    from services.terminal_service import terminal_service

    sid = str(request.state.auth_payload["sid"])
    # Outstanding privileged tickets must not survive an explicit revocation.
    await db.execute(
        update(TerminalTicket)
        .where(
            TerminalTicket.user_id == user.id,
            TerminalTicket.sid == sid,
            TerminalTicket.mode == "admin",
            TerminalTicket.consumed_at.is_(None),
        )
        .values(consumed_at=datetime.now(timezone.utc))
    )
    await terminal_service.close_user_privileged(user.id, "step-up revoked")
    _clear_step_up_cookie(response)


@router.post("/password", status_code=204)
async def change_password(
    body: PasswordChange,
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not verify_password(body.current_password, user.password):
        raise HTTPException(status_code=400, detail="当前密码不正确")
    user.password = hash_password(body.new_password)
    user.token_version += 1
    db.add(AuditLog(user_id=user.id, action="auth.password.change", resource=user.id))
    await db.commit()
    await manager.disconnect_user(user.id, reason="Password changed")
    _clear_step_up_cookie(response)
    _set_auth_cookies(response, user, sid=generate_session_id())


@router.post("/logout", status_code=204)
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    user.token_version += 1
    db.add(AuditLog(user_id=user.id, action="auth.logout", resource=user.id))
    await db.commit()
    await manager.disconnect_user(user.id, reason="Logged out")
    _clear_auth_cookies(response)


@router.get("/users", response_model=list[UserResponse])
async def list_users(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_permission("users.read")),
):
    users = (await db.execute(select(User).order_by(User.username))).scalars().all()
    return [_response(item) for item in users]


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("users.manage")),
):
    if await db.scalar(select(User.id).where(User.username == body.username)):
        raise HTTPException(status_code=409, detail="用户名已存在")
    user = User(username=body.username, password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.flush()
    db.add(AuditLog(user_id=admin.id, action="auth.user.create", resource=user.id))
    return _response(user)


@router.patch("/users/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("users.manage")),
):
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=404, detail="用户不存在")
    if target.id == admin.id and body.disabled is True:
        raise HTTPException(status_code=409, detail="不能禁用当前管理员账户")
    removes_enabled_admin = (
        target.role == "admin"
        and not target.disabled
        and (body.role == "user" or body.disabled is True)
    )
    if removes_enabled_admin:
        remaining = await db.scalar(select(func.count(User.id)).where(
            User.role == "admin",
            User.disabled.is_(False),
            User.id != target.id,
        ))
        if not remaining:
            raise HTTPException(status_code=409, detail="系统必须保留至少一个启用的管理员")
    changed = False
    if body.role is not None and body.role != target.role:
        target.role = body.role
        changed = True
    if body.disabled is not None and body.disabled != target.disabled:
        target.disabled = body.disabled
        changed = True
    if changed:
        target.token_version += 1
        db.add(AuditLog(
            user_id=admin.id,
            action="auth.user.update",
            resource=target.id,
        ))
        await db.commit()
        await manager.disconnect_user(target.id, reason="Account authorization changed")
    return _response(target)
