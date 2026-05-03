#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""配置管理：从环境变量加载，提供默认值。"""

import os
from pathlib import Path


class Settings:
    """应用配置"""

    # ── 服务 ──
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "true").lower() == "true"

    # ── 数据库 ──
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite+aiosqlite:///{Path(__file__).parent / 'data' / 'cluster.db'}"
    )

    # ── JWT ──
    JWT_SECRET: str = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE", "15"))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE", "7"))

    # ── Agent ──
    AGENT_TOKEN: str = os.getenv("AGENT_TOKEN", "dev-agent-token-change-in-production")

    # ── CORS ──
    CORS_ORIGINS: list[str] = os.getenv(
        "CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
    ).split(",")


settings = Settings()
