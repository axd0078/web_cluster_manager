from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from middleware.auth import get_current_user, require_permission
from models.node import Alert, AlertRule

router = APIRouter(prefix="/api/v2/alerts", tags=["alerts"])


@router.get("/")
async def list_alerts(
    limit: int = Query(50, le=500),
    severity: str | None = Query(None),
    resolved: bool | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("cluster.read")),
):
    q = select(Alert).order_by(Alert.created.desc())
    if severity:
        q = q.where(Alert.severity == severity)
    if resolved is not None:
        q = q.where(Alert.resolved == resolved)
    q = q.limit(limit)
    result = await db.execute(q)
    alerts = result.scalars().all()
    return [
        {
            "id": a.id, "node_id": a.node_id, "rule_id": a.rule_id,
            "severity": a.severity, "message": a.message,
            "resolved": a.resolved,
            "created": a.created.isoformat() if a.created else None,
        }
        for a in alerts
    ]


@router.post("/{alert_id}/resolve")
async def resolve_alert(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("alerts.resolve")),
):
    result = await db.execute(select(Alert).where(Alert.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=404, detail="告警不存在")
    alert.resolved = True
    await db.flush()
    return {"status": "resolved"}


# ── Alert Rules ──

@router.get("/rules")
async def list_rules(
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("cluster.read")),
):
    result = await db.execute(select(AlertRule).order_by(AlertRule.name))
    rules = result.scalars().all()
    return [
        {
            "id": r.id, "name": r.name, "metric": r.metric,
            "condition": r.condition, "threshold": r.threshold,
            "duration": r.duration, "enabled": r.enabled,
            "channels": r.channels,
        }
        for r in rules
    ]


@router.post("/rules", status_code=201)
async def create_rule(
    name: str = Query(...),
    metric: str = Query(...),
    condition: str = Query(">"),
    threshold: float = Query(...),
    duration: int = Query(60),
    channels: str = Query("[]"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("alerts.manage")),
):
    rule = AlertRule(
        name=name, metric=metric, condition=condition,
        threshold=threshold, duration=duration, channels=channels,
    )
    db.add(rule)
    await db.flush()
    return {
        "id": rule.id, "name": rule.name, "metric": rule.metric,
        "condition": rule.condition, "threshold": rule.threshold,
        "duration": rule.duration, "enabled": rule.enabled,
        "channels": rule.channels,
    }


@router.put("/rules/{rule_id}")
async def update_rule(
    rule_id: str,
    name: str | None = Query(None),
    metric: str | None = Query(None),
    condition: str | None = Query(None),
    threshold: float | None = Query(None),
    duration: int | None = Query(None),
    enabled: bool | None = Query(None),
    channels: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_permission("alerts.manage")),
):
    result = await db.execute(select(AlertRule).where(AlertRule.id == rule_id))
    rule = result.scalar_one_or_none()
    if rule is None:
        raise HTTPException(status_code=404, detail="规则不存在")
    if name is not None: rule.name = name
    if metric is not None: rule.metric = metric
    if condition is not None: rule.condition = condition
    if threshold is not None: rule.threshold = threshold
    if duration is not None: rule.duration = duration
    if enabled is not None: rule.enabled = enabled
    if channels is not None: rule.channels = channels
    await db.flush()
    return {"status": "updated"}
