from __future__ import annotations

import asyncio
import json
import time

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from config import settings
from core.connection_manager import FrontendPrincipal, manager
from core.security import decode_token
from core.task_engine import task_engine
from database import async_session
from models.user import User
from services.agent_service import authenticate_agent_token, mark_node_offline

router = APIRouter(tags=["websocket"])


def _bearer_from_websocket(ws: WebSocket) -> str | None:
    header = ws.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


async def _authenticate_frontend(ws: WebSocket) -> FrontendPrincipal | None:
    origin = ws.headers.get("origin")
    if origin and origin not in settings.CORS_ORIGINS:
        return None
    token = ws.cookies.get(settings.ACCESS_COOKIE_NAME)
    if not token:
        return None
    try:
        payload = decode_token(token, expected_type="access")
    except ValueError:
        return None
    async with async_session() as db:
        result = await db.execute(select(User).where(User.id == payload.get("sub")))
        user = result.scalar_one_or_none()
        if user is None or int(payload.get("ver", -1)) != user.token_version:
            return None
        try:
            expires_at = float(payload["exp"])
        except (KeyError, TypeError, ValueError):
            return None
        if expires_at <= time.time():
            return None
        return FrontendPrincipal(
            user_id=user.id,
            role=user.role,
            token_version=user.token_version,
            expires_at=expires_at,
        )


def _task_result_event(node_id: str, request_id: str, payload: dict) -> dict:
    """Expose only a typed status signal; Agent-controlled identifiers are ignored."""
    return {
        "type": "task_result",
        "payload": {
            "success": payload.get("success") is True,
            "node_id": node_id,
            "request_id": request_id,
        },
    }


@router.websocket("/ws/agent")
async def agent_ws(ws: WebSocket):
    token = _bearer_from_websocket(ws)
    node = await authenticate_agent_token(token) if token else None
    if node is None:
        await ws.close(code=4001, reason="Invalid agent credential")
        return

    await ws.accept()
    await manager.agent_connect(ws, node.id)
    try:
        while True:
            raw = await ws.receive_text()
            if len(raw) > 2_000_000:
                await ws.close(code=1009, reason="Message too large")
                break
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_json({"type": "error", "payload": {"error": "invalid JSON"}})
                continue
            if not isinstance(msg, dict):
                continue
            msg_type = msg.get("type", "")
            request_id = str(msg.get("request_id") or "")
            payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}

            if msg_type == "monitor_data":
                await manager.handle_monitor_data(node.id, payload)
            elif msg_type == "container_inventory":
                await manager.handle_container_inventory(node.id, payload)
            elif msg_type == "task_result":
                await task_engine.handle_task_result(node.id, request_id, payload)
                manager.resolve_request(node.id, request_id, payload)
                await manager.broadcast_to_frontends(
                    _task_result_event(node.id, request_id, payload),
                    allowed_roles={"admin", "operator"},
                )
            elif msg_type == "file_transfer_result":
                # Chunk acknowledgements are internal request/response traffic.
                # Only the transfer service emits sanitized aggregate progress.
                manager.resolve_request(node.id, request_id, payload)
            elif msg_type in {
                "container_result", "container_logs_result", "browse_result",
                "update_result", "terminal_output",
            }:
                # These are request/response messages for privileged REST calls.
                # Do not publish their raw contents to unrelated browser sockets.
                manager.resolve_request(node.id, request_id, payload)
            elif msg_type == "pong":
                continue
            else:
                await ws.send_json({
                    "type": "error", "request_id": request_id,
                    "payload": {"error": f"unsupported message type: {msg_type}"},
                })
    except WebSocketDisconnect:
        pass
    except Exception:
        # The connection is closed below; avoid leaking internals to the peer.
        pass
    finally:
        await manager.agent_disconnect(ws)
        if node.id not in manager.get_connected_agents():
            await mark_node_offline(node.id)


@router.websocket("/ws/frontend")
async def frontend_ws(ws: WebSocket):
    user = await _authenticate_frontend(ws)
    if user is None:
        await ws.close(code=4001, reason="Authentication required")
        return
    await manager.frontend_connect(ws, user)
    try:
        while True:
            remaining = user.remaining_seconds()
            if remaining <= 0:
                await ws.close(code=4001, reason="Session expired")
                break
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=remaining)
            except asyncio.TimeoutError:
                await ws.close(code=4001, reason="Session expired")
                break
            if raw == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.frontend_disconnect(ws)
