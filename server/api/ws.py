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
from services.terminal_service import terminal_service
from services.update_service import update_service

router = APIRouter(tags=["websocket"])


def _production_websocket_is_secure(ws: WebSocket) -> bool:
    return (
        settings.ENVIRONMENT.lower() != "production"
        or str(ws.scope.get("scheme") or "").lower() == "wss"
    )


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
        if (
            user is None
            or user.disabled
            or int(payload.get("ver", -1)) != user.token_version
            or not payload.get("sid")
        ):
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
            sid=str(payload["sid"]),
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
    if not _production_websocket_is_secure(ws):
        await ws.close(code=4003, reason="WSS required")
        return
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
                manager.mark_agent_monitor(node.id, ws, payload)
            elif msg_type == "container_inventory":
                await manager.handle_container_inventory(node.id, payload)
            elif msg_type == "agent_capabilities":
                protocol = payload.get("task_protocol")
                raw_profiles = payload.get("task_profiles")
                task_compatible = protocol == 2 and isinstance(raw_profiles, dict)
                if not isinstance(raw_profiles, dict):
                    raw_profiles = {}
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
                update_capabilities_valid = False
                async with async_session() as db:
                    current = await db.scalar(select(Node).where(Node.id == node.id))
                    if current:
                        try:
                            capabilities = json.loads(current.capabilities or "{}")
                        except json.JSONDecodeError:
                            capabilities = {}
                        if not isinstance(capabilities, dict):
                            capabilities = {}
                        capabilities["task_protocol"] = 2 if task_compatible else 0
                        capabilities["task_profiles"] = profiles if task_compatible else {}
                        raw_terminal_profiles = payload.get("terminal_profiles", [])
                        if not isinstance(raw_terminal_profiles, list):
                            raw_terminal_profiles = []
                        capabilities["terminal_protocol"] = (
                            3 if payload.get("terminal_protocol") == 3 else 0
                        )
                        capabilities["terminal_profiles"] = sorted({
                            value for value in raw_terminal_profiles
                            if isinstance(value, str)
                            and 0 < len(value) <= 64
                            and all(ch.isalnum() or ch in "_.-" for ch in value)
                        })
                        if payload.get("update_protocol") == 2:
                            updater_version = str(payload.get("updater_version") or "")[:50]
                            os_family = str(payload.get("os_family") or "").lower()
                            architecture = str(
                                payload.get("architecture") or payload.get("arch") or ""
                            ).lower()
                            python_abi = str(payload.get("python_abi") or "").lower()
                            active_release_id = str(payload.get("active_release_id") or "")[:80]
                            active_version = str(payload.get("active_version") or "")[:50]
                            if (
                                os_family in {"windows", "linux"}
                                and architecture in {"amd64", "x86_64", "arm64", "aarch64"}
                                and python_abi.startswith("cp3")
                                and updater_version
                            ):
                                capabilities.update({
                                    "update_protocol": 2,
                                    "updater_version": updater_version,
                                    "os_family": os_family,
                                    "architecture": architecture,
                                    "python_abi": python_abi,
                                    "active_release_id": active_release_id or None,
                                    "active_version": active_version or None,
                                })
                                update_capabilities_valid = True
                        else:
                            capabilities["update_protocol"] = 0
                        current.capabilities = json.dumps(capabilities, sort_keys=True)
                        await db.commit()
                if update_capabilities_valid:
                    manager.mark_agent_capabilities(node.id, ws)
            elif msg_type == "update_health":
                if manager.agent_update_ready(node.id, ws):
                    accepted, message = await update_service.handle_health(node.id, payload)
                else:
                    accepted = False
                    message = "等待当前连接完成能力上报和首轮监控数据"
                await ws.send_json({
                    "type": "update_health_ack",
                    "request_id": request_id,
                    "payload": {
                        "success": accepted,
                        "message": message,
                        "execution_id": str(payload.get("execution_id") or ""),
                        "release_id": str(payload.get("release_id") or ""),
                        "version": str(payload.get("version") or ""),
                    },
                })
            elif msg_type == "update_status":
                await update_service.handle_status(node.id, payload)
            elif msg_type.startswith("terminal_"):
                await terminal_service.handle_target_message(
                    node.id,
                    msg,
                    target_kind="agent",
                )
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
        await terminal_service.target_disconnected(node.id, "agent")
        if node.id not in manager.get_connected_agents():
            await mark_node_offline(node.id)
            await task_service.pause_node_tasks(node.id)
            await update_service.pause_node(node.id)


@router.websocket("/ws/frontend")
async def frontend_ws(ws: WebSocket):
    if not _production_websocket_is_secure(ws):
        await ws.close(code=4003, reason="WSS required")
        return
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
