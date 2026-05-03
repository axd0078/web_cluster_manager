#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebSocket API 路由

- /ws/agent/{node_id} — Agent 长连接
- /ws/ui — 前端 UI 长连接
"""

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_connection_manager, get_db
from config import settings
from core.connection_manager import ConnectionManager
from core.security import verify_token
from models.node import Node
from models.user import User

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/agent/{node_id}")
async def agent_ws(
    ws: WebSocket,
    node_id: str,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """Agent WebSocket 端点：客户端通过此端点维持长连接。

    Query 参数：
    - token: Agent 预共享 Token
    """
    # 验证 Agent Token
    if token != settings.AGENT_TOKEN:
        await ws.close(code=4001, reason="Invalid agent token")
        return

    await ws.accept()
    cm = get_connection_manager()
    await cm.agent_connect(node_id, ws)

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await ws.send_text(json.dumps({
                    "type": "error",
                    "payload": {"message": "Invalid JSON"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))
                continue

            msg_type = msg.get("type", "")

            if msg_type == "heartbeat":
                # 心跳 — 更新心跳时间
                await cm.agent_heartbeat(node_id)
                # 更新数据库
                payload = msg.get("payload", {})
                result = await db.execute(select(Node).where(Node.ip == node_id))
                node = result.scalar_one_or_none()
                if not node:
                    result = await db.execute(select(Node).where(Node.hostname == node_id))
                    node = result.scalar_one_or_none()
                if node:
                    node.status = "online"
                    node.last_seen = datetime.now(timezone.utc)
                    if payload.get("version"):
                        node.version = payload["version"]
                    await db.commit()

                # 广播监控数据给 UI
                await cm._broadcast_ui({
                    "type": "heartbeat",
                    "payload": {"node_id": node_id, **payload},
                    "timestamp": msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                })

            elif msg_type == "command_result":
                # 命令执行结果 — 转发给 UI
                await cm._broadcast_ui({
                    "type": "command_result",
                    "payload": {
                        "node_id": node_id,
                        **msg.get("payload", {}),
                    },
                    "timestamp": msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                })

            elif msg_type == "monitor_data":
                # 监控数据 — 广播给 UI
                await cm._broadcast_ui({
                    "type": "monitor_data",
                    "payload": {
                        "node_id": node_id,
                        **msg.get("payload", {}),
                    },
                    "timestamp": msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                })

            elif msg_type == "register":
                # Agent 注册信息
                payload = msg.get("payload", {})
                result = await db.execute(select(Node).where(Node.ip == node_id))
                node = result.scalar_one_or_none()
                if not node:
                    node = Node(
                        ip=node_id,
                        hostname=payload.get("hostname", ""),
                        os=payload.get("os", ""),
                        version=payload.get("version", "1.0.0"),
                        status="online",
                    )
                    db.add(node)
                else:
                    node.hostname = payload.get("hostname", node.hostname)
                    node.os = payload.get("os", node.os)
                    node.version = payload.get("version", node.version)
                    node.status = "online"
                    node.last_seen = datetime.now(timezone.utc)
                await db.commit()

                await cm._broadcast_ui({
                    "type": "node_registered",
                    "payload": {"node_id": node_id, **payload},
                    "timestamp": msg.get("timestamp", datetime.now(timezone.utc).isoformat()),
                })

            else:
                await ws.send_text(json.dumps({
                    "type": "error",
                    "payload": {"message": f"Unknown message type: {msg_type}"},
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }))

    except WebSocketDisconnect:
        logger.info(f"Agent {node_id} disconnected (WebSocket)")
    except Exception as e:
        logger.error(f"Agent {node_id} error: {e}")
    finally:
        # 标记节点离线
        result = await db.execute(select(Node).where(Node.ip == node_id))
        node = result.scalar_one_or_none()
        if not node:
            result = await db.execute(select(Node).where(Node.hostname == node_id))
            node = result.scalar_one_or_none()
        if node:
            node.status = "offline"
            node.last_seen = datetime.now(timezone.utc)
            await db.commit()
        await cm.agent_disconnect(node_id)


@router.websocket("/ws/ui")
async def ui_ws(
    ws: WebSocket,
    token: str = Query(...),
    db: AsyncSession = Depends(get_db),
):
    """前端 UI WebSocket 端点：浏览器通过此端点接收实时推送。

    Query 参数：
    - token: 用户 JWT access token
    """
    # 验证用户 JWT
    payload = verify_token(token)
    if not payload:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    user_id = payload.get("sub")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        await ws.close(code=4002, reason="User not found")
        return

    await ws.accept()
    cm = get_connection_manager()
    await cm.ui_connect(ws)

    # 发送当前在线节点列表
    await ws.send_text(json.dumps({
        "type": "initial_state",
        "payload": {
            "online_nodes": cm.get_online_nodes(),
        },
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }))

    try:
        while True:
            raw = await ws.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "")

            if msg_type == "command":
                # 前端发送命令给 agent
                target = msg.get("target", "")
                command = msg.get("command", "")
                params = msg.get("params", {})
                request_id = msg.get("request_id", "")

                if target and command:
                    success = await cm.send_to_agent(target, {
                        "type": "command",
                        "payload": {
                            "command": command,
                            "params": params,
                            "request_id": request_id,
                        },
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                    if not success:
                        await ws.send_text(json.dumps({
                            "type": "command_result",
                            "payload": {
                                "request_id": request_id,
                                "status": "error",
                                "message": f"Agent {target} not connected",
                            },
                            "timestamp": datetime.now(timezone.utc).isoformat(),
                        }))

    except WebSocketDisconnect:
        logger.info(f"UI user {user.username} disconnected")
    except Exception as e:
        logger.error(f"UI WebSocket error: {e}")
    finally:
        await cm.ui_disconnect(ws)
