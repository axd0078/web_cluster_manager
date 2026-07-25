from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from core.connection_manager import manager
from database import async_session
from api.ws import _authenticate_frontend
from config import settings
from models.node import Node

router = APIRouter(tags=["terminal"])


@router.websocket("/ws/terminal/{node_id}")
async def terminal_ws(
    ws: WebSocket,
    node_id: str,
):
    """WebSocket terminal — browser connects here, proxies commands to agent."""
    user = await _authenticate_frontend(ws)
    if user is None or user.role != "admin" or not settings.ENABLE_REMOTE_COMMANDS:
        await ws.close(code=4003, reason="Remote terminal disabled or unauthorized")
        return
    # Verify node exists
    async with async_session() as db:
        result = await db.execute(select(Node).where(Node.id == node_id))
        if result.scalar_one_or_none() is None:
            await ws.close(code=4001, reason="Unknown node")
            return

    await manager.authenticated_connect(ws, user)

    # Check agent is online
    if node_id not in manager.get_connected_agents():
        await ws.send_json({"type": "error", "message": "节点不在线"})
        await ws.close()
        await manager.authenticated_disconnect(ws)
        return

    # Create a pending response queue
    pending: dict[str, asyncio.Future] = {}

    async def agent_listener():
        """Receive terminal output from agent via the agent WS and forward to browser."""
        # We register this frontend WS to receive task_result messages
        # The agent sends back stdout via the agent WS, which is processed
        # in ws.py and forwarded to frontends. Here we simply keep the
        # frontend WS alive and proxy commands.
        pass

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
            msg = json.loads(raw)

            if msg.get("type") == "input":
                # Send keystroke/command to agent
                req_id = str(uuid.uuid4())
                sent = await manager.send_to_agent(node_id, {
                    "type": "terminal_input",
                    "request_id": req_id,
                    "payload": {"data": msg.get("data", "")},
                })
                if not sent:
                    await ws.send_json({"type": "error", "message": "发送失败，节点可能已离线"})

            elif msg.get("type") == "resize":
                await manager.send_to_agent(node_id, {
                    "type": "terminal_resize",
                    "payload": {"rows": msg.get("rows", 24), "cols": msg.get("cols", 80)},
                })

            elif msg.get("type") == "ping":
                await ws.send_json({"type": "pong"})

    except WebSocketDisconnect:
        pass
    finally:
        await manager.authenticated_disconnect(ws)
        # Clean up pending futures
        for fut in pending.values():
            if not fut.done():
                fut.cancel()
