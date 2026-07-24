from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select

from core.security import hash_opaque_token
from database import async_session
from models.node import AgentCredential, Node


async def authenticate_agent_token(token: str) -> Node | None:
    token_hash = hash_opaque_token(token)
    async with async_session() as db:
        result = await db.execute(
            select(AgentCredential, Node)
            .join(Node, Node.id == AgentCredential.node_id)
            .where(
                AgentCredential.token_hash == token_hash,
                AgentCredential.revoked_at.is_(None),
            )
        )
        row = result.first()
        if row is None:
            return None
        credential, node = row
        credential.last_used_at = datetime.now(timezone.utc)
        node.credential_state = "active"
        await db.commit()
        return node


async def mark_node_offline(node_id: str) -> None:
    async with async_session() as db:
        result = await db.execute(select(Node).where(Node.id == node_id))
        node = result.scalar_one_or_none()
        if node:
            node.status = "offline"
            node.last_seen = datetime.now(timezone.utc)
            await db.commit()
