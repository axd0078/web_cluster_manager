from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from middleware.auth import get_current_user, require_role
from models.node import Alert, Group, GroupNode, Metric, Node
from schemas.node import (
    ClusterHealth,
    GroupCreate,
    GroupResponse,
    MetricSnapshot,
    NodeRegister,
    NodeResponse,
    NodeStatusUpdate,
)

router = APIRouter(prefix="/api/v2/nodes", tags=["nodes"])


# ── Node CRUD ──

@router.get("/", response_model=list[NodeResponse])
async def list_nodes(
    status: str | None = Query(None),
    group_id: str | None = Query(None),
    search: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    q = select(Node).options(selectinload(Node.containers))
    if status:
        q = q.where(Node.status == status)
    if group_id:
        q = q.join(GroupNode, GroupNode.node_id == Node.id).where(GroupNode.group_id == group_id)
    if search:
        q = q.where(Node.ip.contains(search) | Node.hostname.contains(search))
    q = q.order_by(Node.ip)
    result = await db.execute(q)
    return result.scalars().all()


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(node_id: str, db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Node).options(selectinload(Node.containers)).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return node


@router.post("/", response_model=NodeResponse, status_code=201)
async def register_node(
    body: NodeRegister,
    db: AsyncSession = Depends(get_db),
    _admin=Depends(require_role("admin")),
):
    result = await db.execute(select(Node).options(selectinload(Node.containers)).where(Node.ip == body.ip))
    existing = result.scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if existing:
        existing.hostname = body.hostname or existing.hostname
        existing.os = body.os or existing.os
        existing.version = body.version or existing.version
        existing.status = "online"
        existing.last_seen = now
        await db.flush()
        return existing
    node = Node(
        ip=body.ip, hostname=body.hostname, os=body.os,
        version=body.version, status="offline", last_seen=now,
        credential_state="reenrollment_required",
    )
    db.add(node)
    await db.flush()
    return node


@router.put("/{node_id}/status", response_model=NodeResponse)
async def update_node_status(
    node_id: str, body: NodeStatusUpdate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    result = await db.execute(select(Node).options(selectinload(Node.containers)).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    node.status = body.status
    await db.flush()
    return node


@router.delete("/{node_id}", status_code=204)
async def delete_node(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin")),
):
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    await db.delete(node)


# ── Group CRUD ──

@router.get("/groups/", response_model=list[GroupResponse])
async def list_groups(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    result = await db.execute(select(Group).order_by(Group.name))
    groups = result.scalars().all()
    out = []
    for g in groups:
        cnt = await db.scalar(select(func.count(GroupNode.id)).where(GroupNode.group_id == g.id))
        out.append(GroupResponse(id=g.id, name=g.name, description=g.description, color=g.color, node_count=cnt or 0))
    return out


@router.post("/groups/", response_model=GroupResponse, status_code=201)
async def create_group(
    body: GroupCreate,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    group = Group(name=body.name, description=body.description, color=body.color)
    db.add(group)
    await db.flush()
    return GroupResponse(id=group.id, name=group.name, description=group.description, color=group.color, node_count=0)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin")),
):
    result = await db.execute(select(Group).where(Group.id == group_id))
    group = result.scalar_one_or_none()
    if group is None:
        raise HTTPException(status_code=404, detail="分组不存在")
    await db.delete(group)


@router.post("/groups/{group_id}/nodes/{node_id}", status_code=204)
async def add_node_to_group(
    group_id: str, node_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    gn = GroupNode(group_id=group_id, node_id=node_id)
    db.add(gn)


@router.delete("/groups/{group_id}/nodes/{node_id}", status_code=204)
async def remove_node_from_group(
    group_id: str, node_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    result = await db.execute(
        select(GroupNode).where(GroupNode.group_id == group_id, GroupNode.node_id == node_id)
    )
    gn = result.scalar_one_or_none()
    if gn:
        await db.delete(gn)


# ── Metrics ──

@router.get("/{node_id}/metrics", response_model=list[MetricSnapshot])
async def get_node_metrics(
    node_id: str,
    minutes: int = Query(60, ge=1, le=1440),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    cutoff = datetime.now(timezone.utc).timestamp() - (minutes * 60)
    result = await db.execute(
        select(Metric)
        .where(Metric.node_id == node_id, Metric.time >= func.datetime(cutoff, "unixepoch"))
        .order_by(Metric.time.desc())
        .limit(720)
    )
    metrics = result.scalars().all()
    return [
        MetricSnapshot(
            node_id=m.node_id, cpu_percent=m.cpu_percent,
            mem_percent=m.mem_percent, mem_used=m.mem_used, mem_total=m.mem_total,
            disk_percent=m.disk_percent, disk_used=m.disk_used, disk_total=m.disk_total,
            time=m.time,
        )
        for m in reversed(metrics)
    ]


# ── Dashboard / Health ──

@router.get("/health/summary", response_model=ClusterHealth)
async def cluster_health(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    all_nodes = await db.execute(select(Node))
    nodes = all_nodes.scalars().all()

    total = len(nodes)
    online = sum(1 for n in nodes if n.status == "online")
    offline = sum(1 for n in nodes if n.status == "offline")
    maintenance = sum(1 for n in nodes if n.status == "maintenance")

    # Latest metrics avg
    cpu_vals, mem_vals, disk_vals = [], [], []
    for n in nodes:
        m_result = await db.execute(
            select(Metric).where(Metric.node_id == n.id).order_by(Metric.time.desc()).limit(1)
        )
        m = m_result.scalar_one_or_none()
        if m:
            if m.cpu_percent is not None: cpu_vals.append(m.cpu_percent)
            if m.mem_percent is not None: mem_vals.append(m.mem_percent)
            if m.disk_percent is not None: disk_vals.append(m.disk_percent)

    avg_cpu = sum(cpu_vals) / len(cpu_vals) if cpu_vals else None
    avg_mem = sum(mem_vals) / len(mem_vals) if mem_vals else None
    avg_disk = sum(disk_vals) / len(disk_vals) if disk_vals else None

    active_alerts = await db.scalar(select(func.count(Alert.id)).where(Alert.resolved == False))

    # Simple health score
    score = 100
    if total > 0:
        score -= int((offline / total) * 40)
    if avg_cpu and avg_cpu > 80:
        score -= 15
    if avg_mem and avg_mem > 85:
        score -= 15
    if avg_disk and avg_disk > 85:
        score -= 15
    score = max(0, min(100, score))

    return ClusterHealth(
        total_nodes=total, online_nodes=online, offline_nodes=offline,
        maintenance_nodes=maintenance, avg_cpu=avg_cpu, avg_memory=avg_mem,
        avg_disk=avg_disk, alerts_active=active_alerts or 0, health_score=score,
    )
