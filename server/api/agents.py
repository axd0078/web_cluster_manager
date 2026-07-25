from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.security import generate_opaque_token, hash_opaque_token
from core.connection_manager import manager
from database import get_db
from middleware.auth import require_role
from models.node import AgentCredential, AuditLog, EnrollmentToken, Node
from models.user import User
from schemas.agent import (
    AgentCredentialResponse, AgentEnrollRequest, AgentEnrollResponse,
    EnrollmentTokenCreate, EnrollmentTokenInfo, EnrollmentTokenResponse,
)

router = APIRouter(prefix="/api/v2", tags=["agents"])
bearer = HTTPBearer(auto_error=False)


@router.post("/agent-enrollment-tokens", response_model=EnrollmentTokenResponse, status_code=201)
async def create_enrollment_token(
    body: EnrollmentTokenCreate,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    raw = generate_opaque_token()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=body.ttl_minutes)
    item = EnrollmentToken(
        token_hash=hash_opaque_token(raw), label=body.label,
        created_by=admin.id, expires_at=expires_at,
    )
    db.add(item)
    db.add(AuditLog(
        user_id=admin.id, action="agent.enrollment_token.create",
        resource=item.id, detail=json.dumps({"label": body.label, "ttl": body.ttl_minutes}),
    ))
    await db.flush()
    return EnrollmentTokenResponse(id=item.id, token=raw, expires_at=expires_at)


@router.get("/agent-enrollment-tokens", response_model=list[EnrollmentTokenInfo])
async def list_enrollment_tokens(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_role("admin")),
):
    result = await db.execute(select(EnrollmentToken).order_by(EnrollmentToken.created.desc()).limit(100))
    return result.scalars().all()


@router.delete("/agent-enrollment-tokens/{token_id}", status_code=204)
async def revoke_enrollment_token(
    token_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    result = await db.execute(select(EnrollmentToken).where(EnrollmentToken.id == token_id))
    item = result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404, detail="注册令牌不存在")
    item.revoked_at = datetime.now(timezone.utc)
    db.add(AuditLog(user_id=admin.id, action="agent.enrollment_token.revoke", resource=item.id))


@router.post("/agents/enroll", response_model=AgentEnrollResponse, status_code=201)
async def enroll_agent(
    body: AgentEnrollRequest,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: AsyncSession = Depends(get_db),
):
    if credentials is None:
        raise HTTPException(status_code=401, detail="缺少注册令牌")
    now = datetime.now(timezone.utc)
    token_hash = hash_opaque_token(credentials.credentials)
    result = await db.execute(
        select(EnrollmentToken).where(
            EnrollmentToken.token_hash == token_hash,
            EnrollmentToken.used_at.is_(None),
            EnrollmentToken.revoked_at.is_(None),
            EnrollmentToken.expires_at > now,
        )
    )
    enrollment = result.scalar_one_or_none()
    if enrollment is None:
        raise HTTPException(status_code=401, detail="注册令牌无效、已使用或已过期")
    consumed = await db.execute(
        update(EnrollmentToken)
        .where(
            EnrollmentToken.id == enrollment.id,
            EnrollmentToken.used_at.is_(None),
            EnrollmentToken.revoked_at.is_(None),
            EnrollmentToken.expires_at > now,
        )
        .values(used_at=now)
        .execution_options(synchronize_session=False)
    )
    if consumed.rowcount != 1:
        raise HTTPException(status_code=401, detail="注册令牌已被使用")

    result = await db.execute(select(Node).where(Node.agent_id == body.agent_id))
    node = result.scalar_one_or_none()
    if node is None:
        legacy = await db.execute(select(Node).where(Node.ip == body.ip, Node.agent_id.is_(None)))
        node = legacy.scalar_one_or_none()
    if node is None:
        node = Node(ip=body.ip)
        db.add(node)

    node.agent_id = body.agent_id
    node.ip = body.ip
    node.hostname = body.hostname
    node.os = body.os
    node.platform = body.platform
    node.version = body.version
    node.capabilities = json.dumps(body.capabilities)
    # Enrollment proves possession of a one-time token, not an active Agent
    # WebSocket. The WebSocket handshake is what marks the node online.
    node.status = "offline"
    node.last_seen = now
    node.credential_state = "active"
    await db.flush()

    raw_agent_token = generate_opaque_token()
    existing = await db.execute(select(AgentCredential).where(AgentCredential.node_id == node.id))
    credential = existing.scalar_one_or_none()
    if credential is None:
        credential = AgentCredential(node_id=node.id, token_hash=hash_opaque_token(raw_agent_token))
        db.add(credential)
    else:
        credential.token_hash = hash_opaque_token(raw_agent_token)
        credential.revoked_at = None
        credential.issued_at = now

    db.add(AuditLog(action="agent.enroll", resource=node.id, detail=json.dumps({"agent_id": body.agent_id})))
    await db.flush()
    return AgentEnrollResponse(node_id=node.id, agent_token=raw_agent_token)


@router.post("/agents/{node_id}/credential/rotate", response_model=AgentCredentialResponse)
async def rotate_agent_credential(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    raw = generate_opaque_token()
    result = await db.execute(select(AgentCredential).where(AgentCredential.node_id == node_id))
    credential = result.scalar_one_or_none()
    if credential is None:
        credential = AgentCredential(node_id=node_id, token_hash=hash_opaque_token(raw))
        db.add(credential)
    else:
        credential.token_hash = hash_opaque_token(raw)
        credential.revoked_at = None
        credential.issued_at = datetime.now(timezone.utc)
    node.credential_state = "active"
    db.add(AuditLog(user_id=admin.id, action="agent.credential.rotate", resource=node_id))
    await db.commit()
    await manager.disconnect_agent(node_id)
    return AgentCredentialResponse(node_id=node_id, agent_token=raw)


@router.delete("/agents/{node_id}/credential", status_code=204)
async def revoke_agent_credential(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_role("admin")),
):
    result = await db.execute(select(AgentCredential).where(AgentCredential.node_id == node_id))
    credential = result.scalar_one_or_none()
    if credential is None:
        raise HTTPException(status_code=404, detail="Agent 凭据不存在")
    credential.revoked_at = datetime.now(timezone.utc)
    node_result = await db.execute(select(Node).where(Node.id == node_id))
    node = node_result.scalar_one_or_none()
    if node:
        node.credential_state = "revoked"
    db.add(AuditLog(user_id=admin.id, action="agent.credential.revoke", resource=node_id))
    await db.commit()
    await manager.disconnect_agent(node_id, reason="Credential revoked")
