#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebSocket 连接管理器

管理两类 WebSocket 连接：
- Agent 连接（/ws/agent/{node_id}）：每个客户端一个
- UI 连接（/ws/ui）：每个浏览器用户一个
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    """WebSocket 连接管理器（单例模式，在 lifespan 中创建）"""

    def __init__(self) -> None:
        # node_id -> WebSocket（每个 agent 一个连接）
        self._agents: dict[str, WebSocket] = {}
        # id(WebSocket) -> WebSocket（前端 UI 连接）
        self._ui_clients: dict[int, WebSocket] = {}
        # node_id -> 最后一次心跳时间
        self._heartbeats: dict[str, datetime] = {}
        # 锁（asyncio）
        self._lock = asyncio.Lock()

    # ── Agent 连接管理 ──

    async def agent_connect(self, node_id: str, ws: WebSocket) -> None:
        """Agent 连接建立。"""
        async with self._lock:
            # 如果已有同 node_id 的连接，关闭旧连接
            old = self._agents.pop(node_id, None)
            if old:
                try:
                    await old.close(code=1001, reason="duplicate connection")
                except Exception:
                    pass
            self._agents[node_id] = ws
            self._heartbeats[node_id] = datetime.now(timezone.utc)
        logger.info(f"Agent connected: {node_id} (total agents: {len(self._agents)})")
        await self._broadcast_ui({
            "type": "node_online",
            "payload": {"node_id": node_id},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def agent_disconnect(self, node_id: str) -> None:
        """Agent 断开连接。"""
        async with self._lock:
            ws = self._agents.pop(node_id, None)
            self._heartbeats.pop(node_id, None)
            if ws:
                try:
                    await ws.close()
                except Exception:
                    pass
        logger.info(f"Agent disconnected: {node_id} (total agents: {len(self._agents)})")
        await self._broadcast_ui({
            "type": "node_offline",
            "payload": {"node_id": node_id},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

    async def agent_heartbeat(self, node_id: str) -> None:
        """更新 agent 心跳时间。"""
        async with self._lock:
            self._heartbeats[node_id] = datetime.now(timezone.utc)

    async def send_to_agent(self, node_id: str, data: dict[str, Any]) -> bool:
        """向指定 agent 发送消息。"""
        async with self._lock:
            ws = self._agents.get(node_id)
        if ws:
            try:
                await ws.send_text(json.dumps(data))
                return True
            except Exception:
                await self.agent_disconnect(node_id)
                return False
        return False

    # ── UI 连接管理 ──

    async def ui_connect(self, ws: WebSocket) -> None:
        """UI 连接建立。"""
        async with self._lock:
            self._ui_clients[id(ws)] = ws
        logger.info(f"UI client connected (total: {len(self._ui_clients)})")

    async def ui_disconnect(self, ws: WebSocket) -> None:
        """UI 断开连接。"""
        async with self._lock:
            self._ui_clients.pop(id(ws), None)
        logger.info(f"UI client disconnected (total: {len(self._ui_clients)})")

    async def _broadcast_ui(self, data: dict[str, Any]) -> None:
        """向所有 UI 客户端广播消息。"""
        async with self._lock:
            clients = list(self._ui_clients.values())
        dead: list[WebSocket] = []
        for ws in clients:
            try:
                await ws.send_text(json.dumps(data))
            except Exception:
                dead.append(ws)
        # 清理已断开的连接
        if dead:
            async with self._lock:
                for ws in dead:
                    self._ui_clients.pop(id(ws), None)

    # ── 查询 ──

    def get_online_nodes(self) -> list[str]:
        """返回当前在线节点 ID 列表。"""
        return list(self._agents.keys())

    def get_agent_ids(self) -> list[str]:
        """返回所有 agent 节点 ID。"""
        return list(self._agents.keys())

    def get_heartbeat_time(self, node_id: str) -> datetime | None:
        """返回指定节点的最后心跳时间。"""
        return self._heartbeats.get(node_id)
