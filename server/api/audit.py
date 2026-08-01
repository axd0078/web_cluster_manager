from __future__ import annotations

import csv
import io
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth import require_permission
from models.node import AuditLog

router = APIRouter(prefix="/api/v2/audit", tags=["audit"])


@router.get("/logs")
async def list_logs(
    action: str | None = Query(None),
    user_id: str | None = Query(None),
    resource: str | None = Query(None),
    limit: int = Query(100, le=1000),
    offset: int = Query(0),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("audit.read")),
):
    q = select(AuditLog).order_by(AuditLog.created.desc())
    if action:
        q = q.where(AuditLog.action == action)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    if resource:
        q = q.where(AuditLog.resource.contains(resource))
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    logs = result.scalars().all()
    return [
        {
            "id": log.id, "user_id": log.user_id, "action": log.action,
            "resource": log.resource, "detail": log.detail,
            "ip": log.ip,
            "created": log.created.isoformat() if log.created else None,
        }
        for log in logs
    ]


@router.get("/logs/export")
async def export_logs(
    action: str | None = Query(None),
    user_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("audit.export")),
):
    q = select(AuditLog).order_by(AuditLog.created.desc())
    if action:
        q = q.where(AuditLog.action == action)
    if user_id:
        q = q.where(AuditLog.user_id == user_id)
    q = q.limit(10000)
    result = await db.execute(q)
    logs = result.scalars().all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["id", "user_id", "action", "resource", "detail", "ip", "created"])
    for log in logs:
        writer.writerow([
            log.id, log.user_id or "", log.action, log.resource or "",
            log.detail or "", log.ip or "",
            log.created.isoformat() if log.created else "",
        ])

    output.seek(0)
    filename = f"audit_logs_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
