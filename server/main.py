#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web 集群管理系统 — FastAPI 服务端入口
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings
from database import init_db, async_session
from core.connection_manager import ConnectionManager
from core.security import hash_password
from api.deps import set_connection_manager

# 初始化日志
logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ── 连接管理器单例 ──
connection_manager = ConnectionManager()


# ── 种子数据 ──

async def seed_default_user() -> None:
    """确保默认管理员用户存在。"""
    from sqlalchemy import select
    from models.user import User

    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == "admin"))
        user = result.scalar_one_or_none()
        if not user:
            user = User(
                username="admin",
                password_hash=hash_password("admin123"),
                role="admin",
            )
            db.add(user)
            await db.commit()
            logger.info("Default admin user created (admin / admin123)")
        else:
            logger.info("Admin user already exists")


# ── 应用生命周期 ──

@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan：启动时初始化 DB 和种子数据，关闭时清理。"""
    # 启动
    logger.info("Initializing database...")
    await init_db()
    await seed_default_user()
    set_connection_manager(connection_manager)
    logger.info(f"Server starting on {settings.HOST}:{settings.PORT}")
    yield
    # 关闭
    logger.info("Server shutting down")


# ── 创建 App ──

app = FastAPI(
    title="Web 集群管理系统 API",
    version="2.0.0",
    description="Web Cluster Manager — REST API + WebSocket",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 注册路由 ──

from api.auth import router as auth_router             # noqa: E402
from api.nodes import router as nodes_router           # noqa: E402
from api.ws import router as ws_router                 # noqa: E402

app.include_router(auth_router)
app.include_router(nodes_router)
app.include_router(ws_router)


# ── 直接运行 ──

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
