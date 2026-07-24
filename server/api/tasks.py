from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.connection_manager import manager
from database import get_db
from middleware.auth import get_current_user, require_role
from models.node import Subtask, Task
from models.user import User
from config import settings
from schemas.node import TaskCreate, TaskResponse

router = APIRouter(prefix="/api/v2/tasks", tags=["tasks"])

TASK_TEMPLATES = [
    {"type": "clean_logs", "title": "日志清理", "params": {"path": "logs", "days": 7}},
    {"type": "backup_files", "title": "文件备份", "params": {"source": "", "dest": ""}},
    {"type": "health_check", "title": "健康检查", "params": {}},
    {"type": "batch_command", "title": "批量命令", "params": {"command": ""}},
    {"type": "restart_service", "title": "重启服务", "params": {"service": ""}},
]


@router.get("/", response_model=list[TaskResponse])
async def list_tasks(
    status: str | None = Query(None),
    task_type: str | None = Query(None),
    limit: int = Query(50, le=200),
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    q = select(Task).order_by(Task.created.desc())
    if status:
        q = q.where(Task.status == status)
    if task_type:
        q = q.where(Task.type == task_type)
    q = q.limit(limit)
    result = await db.execute(q)
    tasks = result.scalars().all()

    out = []
    for t in tasks:
        total = await db.scalar(select(func.count(Subtask.id)).where(Subtask.task_id == t.id))
        done = await db.scalar(
            select(func.count(Subtask.id)).where(
                Subtask.task_id == t.id, Subtask.status == "completed"
            )
        )
        out.append(TaskResponse(
            id=t.id, type=t.type, title=t.title, status=t.status,
            created_by=t.created_by, created=t.created, finished=t.finished,
            subtask_count=total or 0, completed_count=done or 0,
        ))
    return out


@router.post("/", response_model=TaskResponse, status_code=201)
async def create_task(
    body: TaskCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    if body.type == "batch_command" and not settings.ENABLE_REMOTE_COMMANDS:
        raise HTTPException(status_code=403, detail="远程命令功能默认关闭")
    task = Task(
        type=body.type, title=body.title,
        params=json.dumps(body.params), created_by=user.username,
    )
    db.add(task)
    await db.flush()

    for node_id in body.target_node_ids:
        st = Subtask(task_id=task.id, node_id=node_id)
        db.add(st)

    # Send command to each connected agent
    for node_id in body.target_node_ids:
        await manager.send_to_agent(node_id, {
            "type": "task",
            "request_id": task.id,
            "payload": {
                "task_type": body.type,
                "params": body.params,
            },
        })

    await db.flush()
    return TaskResponse(
        id=task.id, type=task.type, title=task.title, status=task.status,
        created_by=task.created_by, created=task.created, finished=task.finished,
        subtask_count=len(body.target_node_ids), completed_count=0,
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    subtasks_result = await db.execute(
        select(Subtask).where(Subtask.task_id == task_id).order_by(Subtask.node_id)
    )
    subtasks = subtasks_result.scalars().all()

    return {
        "id": task.id, "type": task.type, "title": task.title,
        "status": task.status, "created_by": task.created_by,
        "created": task.created.isoformat() if task.created else None,
        "finished": task.finished.isoformat() if task.finished else None,
        "params": json.loads(task.params),
        "subtasks": [
            {
                "id": s.id, "node_id": s.node_id, "status": s.status,
                "result": json.loads(s.result) if s.result else None,
                "started": s.started.isoformat() if s.started else None,
                "finished": s.finished.isoformat() if s.finished else None,
            }
            for s in subtasks
        ],
    }


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")
    if task.status not in ("pending", "running"):
        raise HTTPException(status_code=400, detail="只能取消等待中或运行中的任务")
    task.status = "cancelled"
    task.finished = datetime.now(timezone.utc)
    await db.flush()
    return {"status": "cancelled"}


@router.post("/{task_id}/retry")
async def retry_task(
    task_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin", "operator")),
):
    result = await db.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在")

    # Reset failed subtasks
    subtasks_result = await db.execute(
        select(Subtask).where(Subtask.task_id == task_id, Subtask.status == "failed")
    )
    failed_subtasks = list(subtasks_result.scalars().all())
    for st in failed_subtasks:
        st.status = "pending"
        st.result = None

    task.status = "running"
    task.finished = None
    await db.flush()

    # Re-send to agents
    params = json.loads(task.params)
    for st in failed_subtasks:
        await manager.send_to_agent(st.node_id, {
            "type": "task",
            "request_id": task.id,
            "payload": {"task_type": task.type, "params": params},
        })

    return {"status": "retrying"}


@router.get("/templates/list")
async def list_templates(_user=Depends(get_current_user)):
    if settings.ENABLE_REMOTE_COMMANDS:
        return TASK_TEMPLATES
    return [item for item in TASK_TEMPLATES if item["type"] != "batch_command"]
