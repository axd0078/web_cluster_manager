from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import WebSocket
from sqlalchemy import select

from database import async_session
from models.node import ContainerMetric, ContainerResource, Metric, Node


@dataclass(frozen=True)
class FrontendPrincipal:
    user_id: str
    role: str
    token_version: int
    expires_at: float
    sid: str = ""

    def remaining_seconds(self) -> float:
        return max(0.0, self.expires_at - time.time())


@dataclass
class PendingAgentRequest:
    node_id: str
    future: asyncio.Future


@dataclass
class AgentConnectionReadiness:
    websocket: WebSocket
    capabilities_seen: bool = False
    monitor_seen: bool = False


class ConnectionManager:
    """Manages WebSocket connections for both agents and frontend clients."""

    def __init__(self):
        # agent connections: node_id -> WebSocket
        self._agents: dict[str, WebSocket] = {}
        # Privileged Brokers are authenticated independently from Agents.
        self._brokers: dict[str, WebSocket] = {}
        self._ws_to_broker_node: dict[WebSocket, str] = {}
        # Authenticated browser sockets and the subset subscribed to events.
        self._principals: dict[WebSocket, FrontendPrincipal] = {}
        self._frontends: set[WebSocket] = set()
        # node_id from agent registration tokens
        self._ws_to_node: dict[WebSocket, str] = {}
        self._agent_readiness: dict[str, AgentConnectionReadiness] = {}
        self._requests: dict[str, PendingAgentRequest] = {}

    # ── Agent side ──

    async def agent_connect(self, ws: WebSocket, node_id: str):
        old = self._agents.get(node_id)
        if old is not None and old is not ws:
            try:
                await old.close(code=4002, reason="Replaced by a newer authenticated connection")
            except Exception:
                pass
        self._agents[node_id] = ws
        self._ws_to_node[ws] = node_id
        self._agent_readiness[node_id] = AgentConnectionReadiness(websocket=ws)

    async def agent_disconnect(self, ws: WebSocket):
        node_id = self._ws_to_node.pop(ws, None)
        if node_id and self._agents.get(node_id) is ws:
            self._agents.pop(node_id, None)
            self._agent_readiness.pop(node_id, None)

    def mark_agent_capabilities(self, node_id: str, ws: WebSocket) -> bool:
        readiness = self._agent_readiness.get(node_id)
        if readiness is None or readiness.websocket is not ws:
            return False
        readiness.capabilities_seen = True
        return True

    def mark_agent_monitor(self, node_id: str, ws: WebSocket, payload: dict) -> bool:
        readiness = self._agent_readiness.get(node_id)
        if readiness is None or readiness.websocket is not ws:
            return False
        percentages = [
            payload.get("cpu_percent"),
            payload.get("mem_percent"),
            payload.get("disk_percent"),
        ]
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and 0 <= float(value) <= 100
            for value in percentages
        ):
            return False
        readiness.monitor_seen = True
        return True

    def agent_update_ready(self, node_id: str, ws: WebSocket) -> bool:
        readiness = self._agent_readiness.get(node_id)
        return bool(
            readiness is not None
            and readiness.websocket is ws
            and readiness.capabilities_seen
            and readiness.monitor_seen
        )

    async def send_to_agent(self, node_id: str, data: dict) -> bool:
        ws = self._agents.get(node_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            await self.agent_disconnect(ws)
            return False

    async def request_agent(self, node_id: str, data: dict, timeout: float = 15.0) -> dict:
        request_id = data.get("request_id") or str(uuid.uuid4())
        data["request_id"] = request_id
        if request_id in self._requests:
            raise RuntimeError("duplicate Agent request id")
        future = asyncio.get_running_loop().create_future()
        self._requests[request_id] = PendingAgentRequest(node_id=node_id, future=future)
        if not await self.send_to_agent(node_id, data):
            self._requests.pop(request_id, None)
            raise ConnectionError("agent offline")
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        finally:
            self._requests.pop(request_id, None)

    def resolve_request(self, node_id: str, request_id: str, payload: dict) -> bool:
        pending = self._requests.get(request_id)
        if (
            pending is None
            or pending.node_id != node_id
            or pending.future.done()
        ):
            return False
        pending.future.set_result(payload)
        return True

    def get_connected_agents(self) -> list[str]:
        return list(self._agents.keys())

    async def broker_connect(self, ws: WebSocket, node_id: str) -> None:
        old = self._brokers.get(node_id)
        if old is not None and old is not ws:
            try:
                await old.close(code=4002, reason="Replaced by a newer Broker connection")
            except Exception:
                pass
        self._brokers[node_id] = ws
        self._ws_to_broker_node[ws] = node_id

    async def broker_disconnect(self, ws: WebSocket) -> str | None:
        node_id = self._ws_to_broker_node.pop(ws, None)
        if node_id and self._brokers.get(node_id) is ws:
            self._brokers.pop(node_id, None)
        return node_id

    async def send_to_broker(self, node_id: str, data: dict) -> bool:
        ws = self._brokers.get(node_id)
        if ws is None:
            return False
        try:
            await ws.send_json(data)
            return True
        except Exception:
            await self.broker_disconnect(ws)
            return False

    def get_connected_brokers(self) -> list[str]:
        return list(self._brokers)

    async def disconnect_broker(
        self,
        node_id: str,
        code: int = 4001,
        reason: str = "Credential changed",
    ) -> None:
        ws = self._brokers.get(node_id)
        if ws is None:
            return
        try:
            await ws.close(code=code, reason=reason)
        finally:
            await self.broker_disconnect(ws)

    async def disconnect_agent(self, node_id: str, code: int = 4001, reason: str = "Credential changed") -> None:
        ws = self._agents.get(node_id)
        if ws is None:
            return
        try:
            await ws.close(code=code, reason=reason)
        finally:
            await self.agent_disconnect(ws)

    # ── Frontend side ──

    async def authenticated_connect(
        self, ws: WebSocket, principal: FrontendPrincipal, *, subscribe: bool = False,
    ) -> None:
        await ws.accept()
        self._principals[ws] = principal
        if subscribe:
            self._frontends.add(ws)

    async def frontend_connect(self, ws: WebSocket, principal: FrontendPrincipal) -> None:
        await self.authenticated_connect(ws, principal, subscribe=True)

    async def authenticated_disconnect(self, ws: WebSocket) -> None:
        self._principals.pop(ws, None)
        self._frontends.discard(ws)

    async def frontend_disconnect(self, ws: WebSocket) -> None:
        await self.authenticated_disconnect(ws)

    async def disconnect_user(
        self, user_id: str, code: int = 4001, reason: str = "Session revoked",
    ) -> None:
        sockets = [
            ws for ws, principal in self._principals.items()
            if principal.user_id == user_id
        ]
        for ws in sockets:
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass
            finally:
                await self.authenticated_disconnect(ws)

    async def broadcast_to_frontends(
        self,
        data: dict,
        allowed_roles: set[str] | None = None,
        allowed_user_ids: set[str] | None = None,
        unrestricted_roles: set[str] | None = None,
    ) -> None:
        dead: set[WebSocket] = set()
        now = time.time()
        for ws in tuple(self._frontends):
            principal = self._principals.get(ws)
            if principal is None:
                dead.add(ws)
                continue
            if principal.expires_at <= now:
                try:
                    await ws.close(code=4001, reason="Session expired")
                except Exception:
                    pass
                dead.add(ws)
                continue
            if allowed_roles is not None and principal.role not in allowed_roles:
                continue
            if (
                allowed_user_ids is not None
                and principal.user_id not in allowed_user_ids
                and principal.role not in (unrestricted_roles or set())
            ):
                continue
            try:
                await ws.send_json(data)
            except Exception:
                dead.add(ws)
        for ws in dead:
            await self.authenticated_disconnect(ws)

    # ── Monitor data handler ──

    async def handle_monitor_data(self, node_id: str, payload: dict):
        now = datetime.now(timezone.utc)
        async with async_session() as db:
            metric = Metric(
                node_id=node_id,
                time=now,
                cpu_percent=payload.get("cpu_percent"),
                mem_percent=payload.get("mem_percent"),
                mem_used=payload.get("mem_used"),
                mem_total=payload.get("mem_total"),
                disk_percent=payload.get("disk_percent"),
                disk_used=payload.get("disk_used"),
                disk_total=payload.get("disk_total"),
            )
            db.add(metric)
            result = await db.execute(select(Node).where(Node.id == node_id))
            node = result.scalar_one_or_none()
            if node:
                node.last_seen = now
                node.status = "online"
            await db.commit()

        # Broadcast to frontend
        await self.broadcast_to_frontends({
            "type": "monitor_data",
            "payload": {
                "node_id": node_id,
                "cpu_percent": metric.cpu_percent,
                "mem_percent": metric.mem_percent,
                "disk_percent": metric.disk_percent,
                "time": now.isoformat(),
            },
        })

    async def handle_container_inventory(self, node_id: str, payload: dict):
        now = datetime.now(timezone.utc)
        raw_items = payload.get("containers", [])
        items = raw_items if isinstance(raw_items, list) else []
        seen: set[str] = set()
        async with async_session() as db:
            for item in items:
                runtime_id = str(item.get("runtime_id", ""))[:128]
                if not runtime_id:
                    continue
                seen.add(runtime_id)
                result = await db.execute(
                    select(ContainerResource).where(
                        ContainerResource.host_node_id == node_id,
                        ContainerResource.runtime_id == runtime_id,
                    )
                )
                container = result.scalar_one_or_none()
                if container is None:
                    container = ContainerResource(
                        host_node_id=node_id, runtime_id=runtime_id,
                        name=str(item.get("name") or runtime_id[:12])[:255],
                    )
                    db.add(container)
                container.name = str(item.get("name") or container.name)[:255]
                container.image = str(item.get("image") or "")[:500] or None
                container.state = str(item.get("state") or "unknown")[:30]
                container.status = str(item.get("status") or "")[:500] or None
                labels = item.get("labels") if isinstance(item.get("labels"), dict) else {}
                container.labels = json.dumps(labels)
                container.cpu_percent = item.get("cpu_percent")
                container.mem_percent = item.get("mem_percent")
                container.mem_usage = str(item.get("mem_usage") or "")[:100] or None
                container.last_seen = now
                await db.flush()
                db.add(ContainerMetric(
                    container_id=container.id, time=now,
                    cpu_percent=container.cpu_percent,
                    mem_percent=container.mem_percent,
                    mem_usage=container.mem_usage,
                ))
            previous = await db.execute(
                select(ContainerResource).where(ContainerResource.host_node_id == node_id)
            )
            for container in previous.scalars().all():
                if container.runtime_id not in seen:
                    container.state = "removed"
                    container.status = "not present in latest inventory"
            node_result = await db.execute(select(Node).where(Node.id == node_id))
            node = node_result.scalar_one_or_none()
            if node:
                node.last_seen = now
                node.status = "online"
            await db.commit()

        await self.broadcast_to_frontends({
            "type": "container_inventory",
            "payload": {"node_id": node_id, "containers": items, "time": now.isoformat()},
        })


manager = ConnectionManager()
