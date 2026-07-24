from __future__ import annotations

import time

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import (
    create_access_token, create_refresh_token, decode_token, generate_csrf_token,
    hash_password, verify_password,
)
from database import get_db
from middleware.auth import get_current_user, require_role
from models.node import AuditLog
from models.user import User
from schemas.auth import LoginRequest, PasswordChange, UserCreate, UserResponse

router = APIRouter(prefix="/api/v2/auth", tags=["auth"])
_failed_logins: dict[str, list[float]] = {}


def _set_auth_cookies(response: Response, user: User) -> None:
    access = create_access_token(user.id, user.role, user.token_version)
    refresh = create_refresh_token(user.id, user.token_version)
    csrf = generate_csrf_token()
    common = {
        "secure": settings.COOKIE_SECURE,
        "samesite": "strict",
        "domain": settings.COOKIE_DOMAIN,
    }
    response.set_cookie(
        settings.ACCESS_COOKIE_NAME, access, httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60, path="/", **common,
    )
    response.set_cookie(
        settings.REFRESH_COOKIE_NAME, refresh, httponly=True,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        path="/api/v2/auth", **common,
    )
    response.set_cookie(
        settings.CSRF_COOKIE_NAME, csrf, httponly=False,
        max_age=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400, path="/", **common,
    )


def _clear_auth_cookies(response: Response) -> None:
    for name, path in (
        (settings.ACCESS_COOKIE_NAME, "/"),
        (settings.REFRESH_COOKIE_NAME, "/api/v2/auth"),
        (settings.CSRF_COOKIE_NAME, "/"),
    ):
        response.delete_cookie(name, path=path, domain=settings.COOKIE_DOMAIN)


def _check_login_rate_limit(client: str) -> None:
    now = time.monotonic()
    cutoff = now - settings.LOGIN_RATE_LIMIT_WINDOW_SECONDS
    attempts = [value for value in _failed_logins.get(client, []) if value >= cutoff]
    _failed_logins[client] = attempts
    if len(attempts) >= settings.LOGIN_RATE_LIMIT_ATTEMPTS:
        raise HTTPException(status_code=429, detail="登录尝试过多，请稍后重试")


@router.post("/login", response_model=UserResponse)
async def login(
    body: LoginRequest, request: Request, response: Response,
    db: AsyncSession = Depends(get_db),
):
    client = request.client.host if request.client else "unknown"
    _check_login_rate_limit(client)
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password):
        _failed_logins.setdefault(client, []).append(time.monotonic())
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户名或密码错误")
    _failed_logins.pop(client, None)
    db.add(AuditLog(user_id=user.id, action="auth.login", resource=user.id, ip=client))
    _set_auth_cookies(response, user)
    return user


@router.post("/refresh", response_model=UserResponse)
async def refresh(request: Request, response: Response, db: AsyncSession = Depends(get_db)):
    refresh_token = request.cookies.get(settings.REFRESH_COOKIE_NAME)
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="缺少刷新令牌")
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="令牌无效")

    result = await db.execute(select(User).where(User.id == payload["sub"]))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="用户不存在")
    if int(payload.get("ver", -1)) != user.token_version:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="会话已失效")

    _set_auth_cookies(response, user)
    return user


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user


@router.post("/password", status_code=status.HTTP_204_NO_CONTENT)
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
    db.add(user)
    db.add(AuditLog(user_id=user.id, action="auth.password.change", resource=user.id))
    _set_auth_cookies(response, user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _clear_auth_cookies(response)
    user.token_version += 1
    db.add(user)
    db.add(AuditLog(user_id=user.id, action="auth.logout", resource=user.id))


@router.post("/users", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    existing = await db.execute(select(User).where(User.username == body.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="用户名已存在")
    user = User(username=body.username, password=hash_password(body.password), role=body.role)
    db.add(user)
    await db.flush()
    db.add(AuditLog(user_id=_admin.id, action="auth.user.create", resource=user.id))
    return user
