from __future__ import annotations

import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from config import settings
from core.connection_manager import manager
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


async def _authenticate_frontend(ws: WebSocket) -> User | None:
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
        return result.scalar_one_or_none()


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
                manager.resolve_request(request_id, payload)
                await manager.broadcast_to_frontends({
                    "type": "task_result",
                    "payload": {"node_id": node.id, "request_id": request_id, "result": payload},
                })
            elif msg_type == "file_transfer_result":
                # Chunk acknowledgements are internal request/response traffic.
                # Only the transfer service emits sanitized aggregate progress.
                manager.resolve_request(request_id, payload)
            elif msg_type in {
                "container_result", "container_logs_result", "browse_result",
                "update_result", "terminal_output",
            }:
                manager.resolve_request(request_id, payload)
                await manager.broadcast_to_frontends({
                    "type": msg_type,
                    "payload": {"node_id": node.id, "request_id": request_id, **payload},
                })
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
    await manager.frontend_connect(ws)
    try:
        while True:
            raw = await ws.receive_text()
            if raw == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    finally:
        await manager.frontend_disconnect(ws)
