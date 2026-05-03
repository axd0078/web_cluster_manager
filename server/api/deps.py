#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""FastAPI 依赖注入"""

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from core.connection_manager import ConnectionManager

# 全局单例（在 main.py 的 lifespan 中设置）
_connection_manager: ConnectionManager | None = None


def get_connection_manager() -> ConnectionManager:
    if _connection_manager is None:
        raise RuntimeError("ConnectionManager not initialized")
    return _connection_manager


def set_connection_manager(cm: ConnectionManager) -> None:
    global _connection_manager
    _connection_manager = cm


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """获取数据库会话。"""
    async with async_session() as session:
        try:
            yield session
        finally:
            await session.close()
