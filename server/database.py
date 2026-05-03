#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""数据库连接与会话管理（SQLAlchemy async + SQLite）。"""

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from pathlib import Path

from config import settings


# 确保 data 目录存在
Path(settings.DATABASE_URL.replace("sqlite+aiosqlite:///", "")).parent.mkdir(
    parents=True, exist_ok=True
)

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM 基类"""
    pass


async def get_db() -> AsyncSession:
    """FastAPI 依赖注入：获取数据库会话。"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()


async def init_db() -> None:
    """创建所有表（启动时调用）。"""
    from models.node import Node  # noqa: F401
    from models.user import User  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
