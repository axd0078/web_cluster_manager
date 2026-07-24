from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.node import Node


class NodeService:
    """Business logic for node management."""

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_online_nodes(self) -> list[Node]:
        result = await self.db.execute(select(Node).where(Node.status == "online"))
        return list(result.scalars().all())

    async def mark_offline_stale(self, timeout_seconds: int = 90) -> int:
        """Mark nodes offline if they haven't been seen within timeout."""
        from datetime import datetime, timedelta, timezone
        cutoff = datetime.now(timezone.utc) - timedelta(seconds=timeout_seconds)
        result = await self.db.execute(
            select(Node).where(Node.status == "online", Node.last_seen < cutoff)
        )
        stale = result.scalars().all()
        for node in stale:
            node.status = "offline"
        return len(stale)
