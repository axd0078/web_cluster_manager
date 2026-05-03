#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""JWT 签发/验证 + 密码哈希"""

from datetime import datetime, timedelta, timezone
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from config import settings

# ── 密码哈希 ──
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """bcrypt 哈希密码。"""
    return pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码。"""
    return pwd_context.verify(plain, hashed)


# ── JWT ──

def create_token(subject: str, expires_delta: timedelta) -> str:
    """签发 JWT token。"""
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_access_token(user_id: str) -> str:
    """签发 access token（短期，默认 15 分钟）。"""
    return create_token(
        user_id,
        timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    )


def create_refresh_token(user_id: str) -> str:
    """签发 refresh token（长期，默认 7 天）。"""
    return create_token(
        user_id,
        timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


def verify_token(token: str) -> dict[str, Any] | None:
    """验证 JWT token，成功返回 payload，失败返回 None。"""
    try:
        payload = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        )
        return payload
    except JWTError:
        return None
