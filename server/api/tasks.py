from __future__ import annotations

import asyncio
import json
from pathlib import PurePosixPath, PureWindowsPath

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from middleware.auth import require_role
from models.node import AuditLog, Subtask, Task
from models.user import User
from schemas.task import (
    SubtaskResponse,
    TaskCreate,
    TaskDetail,
    TaskResponse,
    TaskRetry,
    TaskTargets,
    TaskTargetsResponse,
)
from services.task_service import _parse_result, resolve_targets, task_service

router = APIRouter(prefix="/api/v2/tasks", tags=["tasks"])

OPERATOR_TASK_TYPES = {"health_check", "clean_logs", "backup_files"}
TASK_TEMPLATES = [
    {"type": "health_check", "title": "健康检查", "params": {}},
    {
        "type": "clean_logs",
        "title": "日志清理",
        "params": {"profile": "", "older_than_days": 7, "dry_run": True},
    },
    {"type": "backup_files", "title": "文件备份", "params": {"profile": "", "source": "."}},
    {"type": "restart_service", "title": "重启服务", "params": {"profile": ""}},
    {"type": "batch_command", "title": "批量命令", "params": {"profile": ""}},
]


def _owned_task_query(task_id: str, user: User):
    query = select(Task).where(Task.id == task_id)
    if user.role != "admin":
        query = query.where(Task.created_by == user.id)
    return query


def _validate_task_role(user: User, task_type: str) -> None:
    if user.role == "operator" and task_type not in OPERATOR_TASK_TYPES:
        raise HTTPException(status_code=403, detail="operator 不能执行此任务类型")
    if task_type == "batch_command" and not settings.ENABLE_REMOTE_COMMANDS:
        raise HTTPException(status_code=403, detail="Server 远程命令开关未开启")


def _validate_relative_source(source: str) -> None:
    normalized = source.replace("\\", "/")
    windows = PureWindowsPath(source)
    posix = PurePosixPath(normalized)
    if (
        windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or normalized.startswith("//")
        or any(part == ".." for part in posix.parts)
    ):
        raise HTTPException(status_code=422, detail="备份源必须是沙箱内的相对路径")


def _progress(subtasks: list[Subtask]) -> int:
    if not subtasks:
        return 0
    values = [
        100 if item.status in {"completed", "cancelled"} else max(0, min(100, item.progress))
        for item in subtasks
    ]
    return sum(values) // len(values)


async def _task_response(
    db: AsyncSession,
    task: Task,
    creator_name: str | None = None,
) -> TaskResponse:
    result = await db.execute(select(Subtask).where(Subtask.task_id == task.id))
    subtasks = list(result.scalars().all())
    if creator_name is None and task.created_by:
        creator_name = await db.scalar(select(User.username).where(User.id == task.created_by))
    return TaskResponse(
        id=task.id,
        type=task.type,
        title=task.title,
        status=task.status,
        created_by=task.created_by,
        created_by_name=creator_name,
        created=task.created,
        started=task.started,
        updated=task.updated,
        finished=task.finished,
        subtask_count=len(subtasks),
        completed_count=sum(item.status == "completed" for item in subtasks),
        progress=_progress(subtasks),
    )


@router.get("/", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = Query(None),
    task_type: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    query = (
        select(Task, User.username)
        .outerjoin(User, User.id == Task.created_by)
        .order_by(Task.created.desc())
    )
    if user.role != "admin":
        query = query.where(Task.created_by == user.id)
    if status:
        query = query.where(Task.status == status)
    if task_type:
        query = query.where(Task.type == task_type)
    result = await db.execute(query.limit(limit))
    return [
        await _task_response(db, task, creator_name)
        for task, creator_name in result.all()
    ]


@router.get("/templates/list")
async def list_templates(user: User = Depends(require_role("admin", "operator"))):
    templates = TASK_TEMPLATES
    if user.role == "operator":
        templates = [item for item in templates if item["type"] in OPERATOR_TASK_TYPES]
    if not settings.ENABLE_REMOTE_COMMANDS:
        templates = [item for item in templates if item["type"] != "batch_command"]
    return templates


@router.post("/targets/resolve", response_model=TaskTargetsResponse)
async def resolve_task_targets(
    body: TaskTargets,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_role("admin", "operator")),
):
    try:
        _, response = await resolve_targets(db, body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return response


@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    _validate_task_role(user, body.type)
    if body.type == "backup_files":
        _validate_relative_source(str(body.params["source"]))
    try:
        nodes, resolved = await resolve_targets(db, body, require_compatible=True)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    profile = body.params.get("profile")
    if profile is not None:
        key = body.type
        if profile not in resolved.common_profiles.get(key, []):
            raise HTTPException(
                status_code=422,
                detail="所选目标未共同提供该配置档",
            )

    target_ids = {node.id for node in nodes}
    if body.type == "clean_logs" and body.params["dry_run"] is False:
        try:
            await task_service.validate_cleanup_preview(
                db,
                creator_id=user.id,
                preview_task_id=str(body.params["preview_task_id"]),
                profile=str(body.params["profile"]),
                older_than_days=int(body.params["older_than_days"]),
                target_node_ids=target_ids,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    task = Task(
        type=body.type,
        title=body.title,
        params=json.dumps(body.params, ensure_ascii=False, sort_keys=True),
        status="queued",
        created_by=user.id,
    )
    db.add(task)
    await db.flush()
    for node in nodes:
        db.add(Subtask(
            task_id=task.id,
            node_id=node.id,
            execution_id=None,
            status="queued",
            message="等待分发",
        ))
    action = "task.create"
    if body.type == "clean_logs":
        action = "task.clean_logs.preview" if body.params["dry_run"] else "task.clean_logs.execute"
    elif body.type in {"restart_service", "batch_command"}:
        action = "task.dangerous.create"
    db.add(AuditLog(
        user_id=user.id,
        action=action,
        resource=task.id,
        detail=json.dumps({"type": body.type, "target_count": len(nodes)}),
    ))
    await db.commit()
    response = await _task_response(db, task, user.username)
    asyncio.create_task(task_service.dispatch_task(task.id))
    return response


@router.get("/{task_id}", response_model=TaskDetail)
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    task = await db.scalar(_owned_task_query(task_id, user))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    result = await db.execute(
        select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.node_id)
    )
    subtasks = list(result.scalars().all())
    base = await _task_response(db, task)
    return TaskDetail(
        **base.model_dump(),
        params=json.loads(task.params),
        subtasks=[
            SubtaskResponse(
                id=item.id,
                node_id=item.node_id,
                execution_id=item.execution_id,
                status=item.status,
                attempts=item.attempts,
                progress=item.progress,
                message=item.message,
                error=item.error,
                result=_parse_result(item.result),
                started=item.started,
                finished=item.finished,
            )
            for item in subtasks
        ],
    )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    task = await db.scalar(_owned_task_query(task_id, user))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _validate_task_role(user, task.type)
    if task.status not in {"queued", "running", "paused"}:
        raise HTTPException(status_code=400, detail="当前任务状态不允许取消")
    db.add(AuditLog(
        user_id=user.id,
        action="task.cancel",
        resource=task.id,
        detail=json.dumps({"type": task.type}),
    ))
    await db.commit()
    status = await task_service.cancel_task(task_id)
    return {"status": status}


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    body: TaskRetry = TaskRetry(),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    task = await db.scalar(_owned_task_query(task_id, user))
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    _validate_task_role(user, task.type)
    db.add(AuditLog(
        user_id=user.id,
        action="task.retry",
        resource=task.id,
        detail=json.dumps({
            "type": task.type,
            "target_count": len(body.target_node_ids or []),
        }),
    ))
    await db.commit()
    try:
        nodes = await task_service.retry_task(task_id, body.target_node_ids)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"status": "retrying", "target_node_ids": nodes}
