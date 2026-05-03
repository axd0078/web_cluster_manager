#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""认证 API 路由"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db
from core.security import (
    verify_password,
    create_access_token,
    create_refresh_token,
    verify_token,
)
from middleware.auth import get_current_user
from models.user import User
from schemas.auth import (
    LoginRequest,
    TokenResponse,
    RefreshRequest,
    UserInfo,
)

router = APIRouter(prefix="/api/v2/auth", tags=["Auth"])


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: AsyncSession = Depends(get_db)):
    """用户登录，返回 JWT token pair。"""
    result = await db.execute(select(User).where(User.username == body.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        )

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInfo(id=user.id, username=user.username, role=user.role),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, db: AsyncSession = Depends(get_db)):
    """刷新 access token。"""
    payload = verify_token(body.refresh_token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
        )

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")

    access_token = create_access_token(user.id)
    refresh_token = create_refresh_token(user.id)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        user=UserInfo(id=user.id, username=user.username, role=user.role),
    )


@router.get("/me", response_model=UserInfo)
async def me(current_user: User = Depends(get_current_user)):
    """获取当前用户信息。"""
    return UserInfo(
        id=current_user.id,
        username=current_user.username,
        role=current_user.role,
    )
