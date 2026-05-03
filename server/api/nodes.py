#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点管理 API 路由"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import get_db, get_connection_manager
from core.connection_manager import ConnectionManager
from middleware.auth import get_current_user
from models.node import Node
from models.user import User
from schemas.node import NodeCreate, NodeResponse, NodeList

router = APIRouter(prefix="/api/v2/nodes", tags=["Nodes"])


@router.get("", response_model=NodeList)
async def list_nodes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cm: ConnectionManager = Depends(get_connection_manager),
):
    """获取所有节点列表（含在线/离线状态）。"""
    result = await db.execute(select(Node).order_by(Node.registered.desc()))
    nodes = result.scalars().all()

    online_ids = set(cm.get_agent_ids())

    node_list = []
    online_count = 0
    for n in nodes:
        is_online = n.ip in online_ids or n.hostname in online_ids
        if is_online:
            online_count += 1
        node_list.append(NodeResponse(
            id=n.id,
            ip=n.ip,
            hostname=n.hostname,
            os=n.os,
            status="online" if is_online else "offline",
            version=n.version,
            tags=n.tags or {},
            metadata_=n.metadata_ or {},
            last_seen=n.last_seen,
            registered=n.registered,
        ))

    return NodeList(
        total=len(node_list),
        online=online_count,
        offline=len(node_list) - online_count,
        nodes=node_list,
    )


@router.get("/{node_id}", response_model=NodeResponse)
async def get_node(
    node_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    cm: ConnectionManager = Depends(get_connection_manager),
):
    """获取单个节点详情。"""
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if not node:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Node not found")

    online_ids = set(cm.get_agent_ids())
    is_online = node.ip in online_ids or node.hostname in online_ids

    return NodeResponse(
        id=node.id,
        ip=node.ip,
        hostname=node.hostname,
        os=node.os,
        status="online" if is_online else "offline",
        version=node.version,
        tags=node.tags or {},
        metadata_=node.metadata_ or {},
        last_seen=node.last_seen,
        registered=node.registered,
    )


@router.post("", response_model=NodeResponse, status_code=status.HTTP_201_CREATED)
async def register_node(
    body: NodeCreate,
    db: AsyncSession = Depends(get_db),
):
    """注册新节点（Agent 自注册，使用 Agent Token 认证而非用户 JWT）。"""
    # 检查是否已存在（按 IP）
    result = await db.execute(select(Node).where(Node.ip == body.ip))
    existing = result.scalar_one_or_none()

    now = datetime.now(timezone.utc)
    if existing:
        existing.hostname = body.hostname or existing.hostname
        existing.os = body.os or existing.os
        existing.version = body.version or existing.version
        existing.last_seen = now
        if body.tags:
            existing.tags = {**existing.tags, **body.tags}
        await db.commit()
        await db.refresh(existing)
        return NodeResponse(
            id=existing.id,
            ip=existing.ip,
            hostname=existing.hostname,
            os=existing.os,
            status="online",
            version=existing.version,
            tags=existing.tags or {},
            metadata_=existing.metadata_ or {},
            last_seen=existing.last_seen,
            registered=existing.registered,
        )

    node = Node(
        ip=body.ip,
        hostname=body.hostname,
        os=body.os,
        version=body.version,
        status="online",
        tags=body.tags,
        metadata_=body.metadata_,
        last_seen=now,
    )
    db.add(node)
    await db.commit()
    await db.refresh(node)

    return NodeResponse(
        id=node.id,
        ip=node.ip,
        hostname=node.hostname,
        os=node.os,
        status="online",
        version=node.version,
        tags=node.tags or {},
        metadata_=node.metadata_ or {},
        last_seen=node.last_seen,
        registered=node.registered,
    )
