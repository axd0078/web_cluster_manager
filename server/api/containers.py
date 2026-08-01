from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.connection_manager import manager
from database import get_db
from middleware.auth import require_permission
from models.node import AuditLog, ContainerResource
from models.user import User
from schemas.agent import ContainerActionRequest, ContainerResponse

router = APIRouter(prefix="/api/v2/containers", tags=["containers"])


@router.get("/host/{node_id}", response_model=list[ContainerResponse])
async def list_host_containers(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("cluster.read")),
):
    result = await db.execute(
        select(ContainerResource)
        .where(ContainerResource.host_node_id == node_id)
        .order_by(ContainerResource.name)
    )
    return result.scalars().all()


@router.post("/{container_id}/actions")
async def container_action(
    container_id: str,
    body: ContainerActionRequest,
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("containers.control")),
):
    result = await db.execute(select(ContainerResource).where(ContainerResource.id == container_id))
    container = result.scalar_one_or_none()
    if container is None:
        raise HTTPException(status_code=404, detail="容器不存在")
    try:
        response = await manager.request_agent(container.host_node_id, {
            "type": "container_action",
            "payload": {"runtime_id": container.runtime_id, "action": body.action},
        }, timeout=30)
    except (ConnectionError, TimeoutError):
        raise HTTPException(status_code=503, detail="宿主 Agent 不在线或响应超时")
    db.add(AuditLog(
        user_id=admin.id, action=f"container.{body.action}", resource=container.id,
        detail=json.dumps({"runtime_id": container.runtime_id, "result": response.get("success")}),
    ))
    if not response.get("success"):
        raise HTTPException(status_code=502, detail=response.get("error", "容器操作失败"))
    return response


@router.get("/{container_id}/logs")
async def container_logs(
    container_id: str,
    tail: int = Query(200, ge=1, le=1000),
    db: AsyncSession = Depends(get_db),
    admin: User = Depends(require_permission("containers.logs")),
):
    result = await db.execute(select(ContainerResource).where(ContainerResource.id == container_id))
    container = result.scalar_one_or_none()
    if container is None:
        raise HTTPException(status_code=404, detail="容器不存在")
    try:
        response = await manager.request_agent(container.host_node_id, {
            "type": "container_logs",
            "payload": {"runtime_id": container.runtime_id, "tail": tail},
        }, timeout=20)
    except (ConnectionError, TimeoutError):
        raise HTTPException(status_code=503, detail="宿主 Agent 不在线或响应超时")
    db.add(AuditLog(
        user_id=admin.id, action="container.logs.read", resource=container.id,
        detail=json.dumps({"tail": tail}),
    ))
    if not response.get("success"):
        raise HTTPException(status_code=502, detail=response.get("error", "读取日志失败"))
    return {"container_id": container.id, "logs": str(response.get("logs", ""))[-200_000:]}
