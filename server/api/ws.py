from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from config import settings
from core.connection_manager import FrontendPrincipal, manager
from core.security import decode_token
from database import async_session
from models.node import Node
from models.user import User
from services.agent_service import authenticate_agent_token, mark_node_offline
from services.task_service import task_service

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
    async with async_session() as db:
        current = await db.scalar(select(Node).where(Node.id == node.id))
        if current:
            current.status = "online"
            current.last_seen = datetime.now(timezone.utc)
            await db.commit()
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
            elif msg_type == "agent_capabilities":
                protocol = payload.get("task_protocol")
                raw_profiles = payload.get("task_profiles")
                if protocol != 2 or not isinstance(raw_profiles, dict):
                    continue
                allowed_keys = {
                    "clean_logs", "backup_files", "restart_service", "batch_command",
                }
                profiles: dict[str, list[str]] = {}
                for key in allowed_keys:
                    values = raw_profiles.get(key, [])
                    if not isinstance(values, list):
                        values = []
                    profiles[key] = sorted({
                        value for value in values
                        if isinstance(value, str)
                        and 0 < len(value) <= 64
                        and all(ch.isalnum() or ch in "_.-" for ch in value)
                    })
                async with async_session() as db:
                    current = await db.scalar(select(Node).where(Node.id == node.id))
                    if current:
                        try:
                            capabilities = json.loads(current.capabilities or "{}")
                        except json.JSONDecodeError:
                            capabilities = {}
                        if not isinstance(capabilities, dict):
                            capabilities = {}
                        capabilities["task_protocol"] = 2
                        capabilities["task_profiles"] = profiles
                        current.capabilities = json.dumps(capabilities, sort_keys=True)
                        await db.commit()
            elif msg_type == "task_ack":
                manager.resolve_request(node.id, request_id, payload)
            elif msg_type == "task_progress":
                await task_service.handle_progress(node.id, request_id, payload)
            elif msg_type == "task_result":
                await task_service.handle_result(node.id, request_id, payload)
                await manager.broadcast_to_frontends(
                    _task_result_event(node.id, request_id, payload),
                    allowed_roles={"admin"},
                )
            elif msg_type == "task_cancel_ack":
                manager.resolve_request(node.id, request_id, payload)
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
            await task_service.pause_node_tasks(node.id)


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
