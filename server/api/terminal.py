from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    WebSocket,
    WebSocketDisconnect,
)
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from api.ws import _authenticate_frontend, _bearer_from_websocket
from config import settings
from core.connection_manager import manager
from core.permissions import has_permissions
from core.security import generate_opaque_token, hash_opaque_token
from database import async_session, get_db
from middleware.auth import get_current_user, require_permission
from models.node import AuditLog, Node
from models.terminal import BrokerCredential, TerminalSession, TerminalTicket
from models.user import User
from services.terminal_service import terminal_service

router = APIRouter(tags=["terminal"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _secure_transport(scope) -> bool:
    scheme = str(scope.get("scheme") or "").lower()
    if scheme in {"https", "wss"}:
        return True
    if settings.ENVIRONMENT.lower() != "production":
        client = scope.get("client") or ("", 0)
        server = scope.get("server") or ("", 0)
        return (
            str(client[0]) in {"127.0.0.1", "::1", "localhost", "testclient"}
            or str(server[0]) in {"127.0.0.1", "::1", "localhost", "testserver"}
        )
    return False


class TicketCreate(BaseModel):
    node_id: str
    mode: Literal["low", "admin"]
    profile: str | None = Field(None, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class BrokerCredentialResponse(BaseModel):
    node_id: str
    broker_token: str


def _node_capabilities(node: Node) -> dict:
    try:
        value = json.loads(node.capabilities or "{}")
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


@router.get("/api/v2/terminal/capabilities")
async def capabilities(
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("cluster.read")),
):
    secure = _secure_transport(request.scope)
    nodes = (await db.execute(select(Node).order_by(Node.hostname, Node.ip))).scalars().all()
    agent_online = set(manager.get_connected_agents())
    broker_online = set(manager.get_connected_brokers())
    can_read_broker = has_permissions(user.role, "brokers.read")
    output = []
    for node in nodes:
        item = _node_capabilities(node)
        aliases = item.get("terminal_profiles")
        aliases = aliases if isinstance(aliases, list) else []
        protocol_ok = item.get("terminal_protocol") == 3
        output.append({
            "node_id": node.id,
            "hostname": node.hostname or node.ip,
            "platform": node.platform,
            "low_profiles": aliases if protocol_ok else [],
            "low_available": bool(
                secure
                and settings.ENABLE_LOW_TERMINAL
                and node.id in agent_online
                and protocol_ok
            ),
            "broker_online": node.id in broker_online if can_read_broker else False,
            "admin_available": bool(
                can_read_broker
                and secure
                and settings.ENABLE_PRIVILEGED_TERMINAL
                and node.id in broker_online
            ),
            "protocol_compatible": protocol_ok,
        })
    return {
        "secure_transport": secure,
        "low_enabled": settings.ENABLE_LOW_TERMINAL,
        "privileged_enabled": settings.ENABLE_PRIVILEGED_TERMINAL,
        "nodes": output,
    }


@router.post("/api/v2/terminal/tickets", status_code=201)
async def create_ticket(
    body: TicketCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if not _secure_transport(request.scope):
        raise HTTPException(status_code=403, detail="终端只允许通过 HTTPS/WSS 使用")
    node = await db.get(Node, body.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    capabilities = _node_capabilities(node)
    if body.mode == "low":
        if "terminal.low" not in request_permissions(request, user):
            raise HTTPException(status_code=403, detail="权限不足")
        aliases = capabilities.get("terminal_profiles", [])
        if (
            not settings.ENABLE_LOW_TERMINAL
            or body.node_id not in manager.get_connected_agents()
            or capabilities.get("terminal_protocol") != 3
            or not body.profile
            or body.profile not in aliases
        ):
            raise HTTPException(status_code=409, detail="低权限终端不可用或别名无效")
    else:
        # The dependency is evaluated explicitly because the required
        # permission depends on the requested mode.
        await require_permission("terminal.admin")(request, user)
        if body.profile is not None:
            raise HTTPException(status_code=422, detail="管理员终端不接受命令别名")
        if (
            not settings.ENABLE_PRIVILEGED_TERMINAL
            or body.node_id not in manager.get_connected_brokers()
        ):
            raise HTTPException(status_code=409, detail="特权 Broker 不在线")
        if terminal_service.privileged_count() >= settings.TERMINAL_MAX_GLOBAL_PRIVILEGED:
            raise HTTPException(status_code=409, detail="特权终端全局并发已达上限")
        if terminal_service.privileged_conflict(user.id, body.node_id):
            raise HTTPException(status_code=409, detail="当前管理员或节点已有特权终端")
    sid = str(request.state.auth_payload["sid"])
    raw = generate_opaque_token()
    expires = _utcnow() + timedelta(seconds=settings.TERMINAL_TICKET_TTL_SECONDS)
    ticket = TerminalTicket(
        token_hash=hash_opaque_token(raw),
        user_id=user.id,
        node_id=body.node_id,
        mode=body.mode,
        profile=body.profile,
        sid=sid,
        expires_at=expires,
    )
    db.add(ticket)
    db.add(AuditLog(
        user_id=user.id,
        action="terminal.ticket.create",
        resource=body.node_id,
        detail=json.dumps({"mode": body.mode}),
        ip=request.client.host if request.client else None,
    ))
    await db.flush()
    return {
        "ticket": raw,
        "expires_at": expires.isoformat(),
        "websocket_path": "/ws/terminal",
    }


def request_permissions(request: Request, user: User) -> set[str]:
    from middleware.auth import permission_snapshot

    permissions, _ = permission_snapshot(request, user)
    return set(permissions)


@router.get("/api/v2/terminal/sessions")
async def list_sessions(
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("terminal.low")),
):
    query = select(TerminalSession).order_by(TerminalSession.created_at.desc()).limit(
        max(1, min(limit, 500))
    )
    if not has_permissions(user.role, "history.all"):
        query = query.where(TerminalSession.user_id == user.id)
    sessions = (await db.execute(query)).scalars().all()
    return [{
        "id": item.id,
        "user_id": item.user_id,
        "node_id": item.node_id,
        "mode": item.mode,
        "status": item.status,
        "bytes_in": item.bytes_in,
        "bytes_out": item.bytes_out,
        "created_at": item.created_at,
        "started_at": item.started_at,
        "ended_at": item.ended_at,
        "end_reason": item.end_reason,
    } for item in sessions]


@router.delete("/api/v2/terminal/sessions/{session_id}", status_code=204)
async def close_session(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("terminal.low")),
):
    session = await db.get(TerminalSession, session_id)
    if session is None or session.user_id != user.id:
        raise HTTPException(status_code=404, detail="终端会话不存在")
    await terminal_service.close(session_id, "closed through API")


@router.get("/api/v2/terminal/brokers")
async def list_brokers(
    db: AsyncSession = Depends(get_db),
    _admin: User = Depends(require_permission("brokers.read")),
):
    credentials = (await db.execute(select(BrokerCredential))).scalars().all()
    online = set(manager.get_connected_brokers())
    return [{
        "node_id": item.node_id,
        "credential_state": "revoked" if item.revoked_at else "active",
        "online": item.node_id in online,
        "issued_at": item.issued_at,
        "last_used_at": item.last_used_at,
    } for item in credentials]


@router.post(
    "/api/v2/terminal/brokers/{node_id}/credential",
    response_model=BrokerCredentialResponse,
)
async def rotate_broker_credential(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("brokers.manage")),
):
    if await db.get(Node, node_id) is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    raw = generate_opaque_token()
    credential = await db.scalar(
        select(BrokerCredential).where(BrokerCredential.node_id == node_id)
    )
    if credential is None:
        credential = BrokerCredential(node_id=node_id, token_hash=hash_opaque_token(raw))
        db.add(credential)
    else:
        credential.token_hash = hash_opaque_token(raw)
        credential.issued_at = _utcnow()
        credential.revoked_at = None
    db.add(AuditLog(
        user_id=admin.id,
        action="broker.credential.rotate",
        resource=node_id,
    ))
    await db.commit()
    await manager.disconnect_broker(node_id, reason="Broker credential rotated")
    return BrokerCredentialResponse(node_id=node_id, broker_token=raw)


@router.delete("/api/v2/terminal/brokers/{node_id}/credential", status_code=204)
async def revoke_broker_credential(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("brokers.manage")),
):
    credential = await db.scalar(
        select(BrokerCredential).where(BrokerCredential.node_id == node_id)
    )
    if credential is None:
        raise HTTPException(status_code=404, detail="Broker 凭据不存在")
    credential.revoked_at = _utcnow()
    db.add(AuditLog(
        user_id=admin.id,
        action="broker.credential.revoke",
        resource=node_id,
    ))
    await db.commit()
    await manager.disconnect_broker(node_id, reason="Broker credential revoked")
    await terminal_service.target_disconnected(node_id, "broker")


async def _consume_ticket(
    raw: str,
    principal,
) -> TerminalTicket | None:
    now = _utcnow()
    token_hash = hash_opaque_token(raw)
    async with async_session() as db:
        ticket = await db.scalar(select(TerminalTicket).where(
            TerminalTicket.token_hash == token_hash,
            TerminalTicket.user_id == principal.user_id,
            TerminalTicket.sid == principal.sid,
            TerminalTicket.expires_at > now,
            TerminalTicket.consumed_at.is_(None),
        ))
        if ticket is None:
            return None
        consumed = await db.execute(
            update(TerminalTicket)
            .where(
                TerminalTicket.id == ticket.id,
                TerminalTicket.consumed_at.is_(None),
                TerminalTicket.expires_at > now,
            )
            .values(consumed_at=now)
            .execution_options(synchronize_session=False)
        )
        if consumed.rowcount != 1:
            await db.rollback()
            return None
        await db.commit()
        # Preserve scalar attributes after the session closes.
        db.expunge(ticket)
        return ticket


@router.websocket("/ws/terminal")
async def terminal_ws(ws: WebSocket):
    if not _secure_transport(ws.scope):
        await ws.close(code=4003, reason="Secure transport required")
        return
    principal = await _authenticate_frontend(ws)
    if principal is None:
        await ws.close(code=4001, reason="Authentication required")
        return
    await manager.authenticated_connect(ws, principal)
    try:
        raw = await asyncio_wait_text(ws)
        if len(raw) > 4096:
            raise ValueError
        message = json.loads(raw)
        if not isinstance(message, dict) or message.get("type") != "auth":
            raise ValueError
        ticket_value = message.get("ticket")
        if not isinstance(ticket_value, str) or len(ticket_value) > 256:
            raise ValueError
        ticket = await _consume_ticket(ticket_value, principal)
        if ticket is None:
            await ws.send_json({"type": "error", "message": "终端票据无效或已使用"})
            await ws.close(code=4003, reason="Invalid terminal ticket")
            return
        await terminal_service.run_browser(ws, principal, ticket)
    except (ValueError, json.JSONDecodeError, asyncio.TimeoutError):
        await ws.close(code=4003, reason="Terminal ticket required")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.authenticated_disconnect(ws)


async def asyncio_wait_text(ws: WebSocket) -> str:
    return await asyncio.wait_for(
        ws.receive_text(),
        timeout=settings.TERMINAL_AUTH_TIMEOUT_SECONDS,
    )


@router.websocket("/ws/broker")
async def broker_ws(ws: WebSocket):
    if not _secure_transport(ws.scope):
        await ws.close(code=4003, reason="Secure transport required")
        return
    token = _bearer_from_websocket(ws)
    if not token:
        await ws.close(code=4001, reason="Broker credential required")
        return
    async with async_session() as db:
        credential = await db.scalar(select(BrokerCredential).where(
            BrokerCredential.token_hash == hash_opaque_token(token),
            BrokerCredential.revoked_at.is_(None),
        ))
        if credential is None:
            await ws.close(code=4001, reason="Invalid Broker credential")
            return
        node_id = credential.node_id
        credential.last_used_at = _utcnow()
        await db.commit()
    await ws.accept()
    await manager.broker_connect(ws, node_id)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 100_000:
                await ws.close(code=1009, reason="Message too large")
                break
            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(message, dict) and str(message.get("type") or "").startswith("terminal_"):
                await terminal_service.handle_target_message(
                    node_id,
                    message,
                    target_kind="broker",
                )
    except WebSocketDisconnect:
        pass
    finally:
        await manager.broker_disconnect(ws)
        await terminal_service.target_disconnected(node_id, "broker")
