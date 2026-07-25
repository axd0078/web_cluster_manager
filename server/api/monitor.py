from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.connection_manager import manager
from api.ws import _authenticate_frontend
from database import get_db
from middleware.auth import get_current_user
from models.node import Alert, Metric, Node

router = APIRouter(prefix="/api/v2/monitor", tags=["monitor"])


@router.websocket("/ws")
async def monitor_ws(ws: WebSocket):
    """Dedicated WebSocket for real-time monitor data (frontend)."""
    principal = await _authenticate_frontend(ws)
    if principal is None:
        await ws.close(code=4001, reason="Authentication required")
        return
    await manager.frontend_connect(ws, principal)
    try:
        while True:
            remaining = principal.remaining_seconds()
            if remaining <= 0:
                await ws.close(code=4001, reason="Session expired")
                break
            try:
                await asyncio.wait_for(ws.receive_text(), timeout=remaining)
            except asyncio.TimeoutError:
                await ws.close(code=4001, reason="Session expired")
                break
    except WebSocketDisconnect:
        pass
    finally:
        await manager.frontend_disconnect(ws)


@router.get("/current")
async def current_snapshot(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    """Return latest metric for every online node."""
    nodes_result = await db.execute(select(Node).where(Node.status == "online"))
    nodes = nodes_result.scalars().all()

    snapshots = []
    for node in nodes:
        m_result = await db.execute(
            select(Metric)
            .where(Metric.node_id == node.id)
            .order_by(Metric.time.desc())
            .limit(1)
        )
        m = m_result.scalar_one_or_none()
        snapshots.append({
            "node_id": node.id,
            "ip": node.ip,
            "hostname": node.hostname,
            "cpu_percent": m.cpu_percent if m else None,
            "mem_percent": m.mem_percent if m else None,
            "disk_percent": m.disk_percent if m else None,
            "time": m.time.isoformat() if m and m.time else None,
        })
    return snapshots


@router.get("/alerts")
async def recent_alerts(
    limit: int = Query(50, le=200),
    resolved: bool | None = None,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    q = select(Alert).order_by(Alert.created.desc())
    if resolved is not None:
        q = q.where(Alert.resolved == resolved)
    q = q.limit(limit)
    result = await db.execute(q)
    alerts = result.scalars().all()
    return [
        {
            "id": a.id, "node_id": a.node_id, "severity": a.severity,
            "message": a.message, "resolved": a.resolved,
            "created": a.created.isoformat() if a.created else None,
        }
        for a in alerts
    ]
