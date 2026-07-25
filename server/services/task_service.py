from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.connection_manager import manager
from database import async_session
from models.node import AuditLog, Group, GroupNode, Node, Subtask, Task
from schemas.task import ResolvedTaskNode, TaskTargets, TaskTargetsResponse

logger = logging.getLogger("server.task")

RETRYABLE_SUBTASK_STATUSES = {"failed", "paused"}
TASK_PROFILE_KEYS = {
    "clean_logs": "clean_logs",
    "backup_files": "backup_files",
    "restart_service": "restart_service",
    "batch_command": "batch_command",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def task_capabilities(node: Node) -> tuple[bool, dict[str, list[str]]]:
    raw = _safe_json(node.capabilities, {})
    if raw.get("task_protocol") != 2:
        return False, {}
    profiles = raw.get("task_profiles")
    if not isinstance(profiles, dict):
        profiles = {}
    sanitized: dict[str, list[str]] = {}
    for key in TASK_PROFILE_KEYS.values():
        values = profiles.get(key, [])
        if not isinstance(values, list):
            values = []
        sanitized[key] = sorted({
            str(value) for value in values
            if isinstance(value, str) and 0 < len(value) <= 64
        })
    return True, sanitized


async def resolve_targets(
    db: AsyncSession,
    targets: TaskTargets,
    *,
    require_compatible: bool = False,
) -> tuple[list[Node], TaskTargetsResponse]:
    node_ids = set(targets.target_node_ids)
    if targets.target_group_ids:
        group_result = await db.execute(
            select(Group.id).where(Group.id.in_(set(targets.target_group_ids)))
        )
        found_groups = set(group_result.scalars().all())
        missing_groups = set(targets.target_group_ids) - found_groups
        if missing_groups:
            raise ValueError("目标分组不存在")
        members = await db.execute(
            select(GroupNode.node_id).where(GroupNode.group_id.in_(found_groups))
        )
        node_ids.update(members.scalars().all())
    if targets.all_online:
        online = await db.execute(select(Node.id).where(Node.status == "online"))
        node_ids.update(online.scalars().all())
    if not node_ids:
        raise ValueError("至少需要解析出一个目标节点")
    if len(node_ids) > settings.MAX_TASK_TARGETS:
        raise ValueError(f"任务目标不能超过 {settings.MAX_TASK_TARGETS} 个 Agent")

    result = await db.execute(select(Node).where(Node.id.in_(node_ids)).order_by(Node.ip))
    nodes = list(result.scalars().all())
    if len(nodes) != len(node_ids):
        raise ValueError("一个或多个目标节点不存在")

    resolved: list[ResolvedTaskNode] = []
    profile_sets: dict[str, list[set[str]]] = {key: [] for key in TASK_PROFILE_KEYS.values()}
    incompatible = False
    for node in nodes:
        compatible, profiles = task_capabilities(node)
        if not compatible:
            incompatible = True
        resolved.append(ResolvedTaskNode(
            id=node.id,
            hostname=node.hostname,
            ip=node.ip,
            platform=node.platform,
            status=node.status,
            compatible=compatible,
            incompatibility=None if compatible else "Agent 不支持 task v2",
        ))
        if compatible:
            for key, values in profiles.items():
                profile_sets[key].append(set(values))

    if require_compatible and incompatible:
        raise ValueError("目标中包含不支持 task v2 的 Agent")
    common: dict[str, list[str]] = {}
    for key, sets in profile_sets.items():
        common[key] = sorted(set.intersection(*sets)) if len(sets) == len(nodes) and sets else []
    return nodes, TaskTargetsResponse(nodes=resolved, common_profiles=common)


def _parse_result(raw: str | None) -> dict | None:
    if not raw:
        return None
    value = _safe_json(raw, {})
    return value or None


class TaskService:
    def __init__(self) -> None:
        self._subtask_locks: dict[str, asyncio.Lock] = {}
        self._task_locks: dict[str, asyncio.Lock] = {}
        self._background_tasks: set[asyncio.Task] = set()

    def schedule_dispatch(
        self, task_id: str, target_node_ids: list[str] | None = None,
    ) -> None:
        task = asyncio.create_task(self.dispatch_task(task_id, target_node_ids))
        self._background_tasks.add(task)
        task.add_done_callback(self._dispatch_finished)

    def _dispatch_finished(self, task: asyncio.Task) -> None:
        self._background_tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Task dispatch failed",
                exc_info=(type(error), error, error.__traceback__),
            )

    async def shutdown(self) -> None:
        tasks = list(self._background_tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._background_tasks.clear()
        await self.recover_after_restart()

    async def recover_after_restart(self) -> None:
        now = utcnow()
        async with async_session() as db:
            result = await db.execute(select(Subtask).where(
                Subtask.status.in_(["queued", "running", "cancel_requested", "pending"])
            ))
            task_ids: set[str] = set()
            for subtask in result.scalars().all():
                subtask.status = "paused"
                subtask.error = "Server restarted before task completion"
                subtask.message = "等待手动重试"
                task_ids.add(subtask.task_id)
            if task_ids:
                tasks = await db.execute(select(Task).where(Task.id.in_(task_ids)))
                for task in tasks.scalars().all():
                    task.status = "paused"
                    task.updated = now
                    task.finished = None
            await db.commit()

    async def dispatch_task(self, task_id: str, target_node_ids: list[str] | None = None) -> None:
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            await self._dispatch_task_locked(task_id, target_node_ids)

    async def _dispatch_task_locked(
        self, task_id: str, target_node_ids: list[str] | None = None,
    ) -> None:
        async with async_session() as db:
            query = select(Subtask.id).where(
                Subtask.task_id == task_id,
                Subtask.status == "queued",
            )
            if target_node_ids is not None:
                query = query.where(Subtask.node_id.in_(target_node_ids))
            result = await db.execute(query)
            subtask_ids = list(result.scalars().all())
        if not subtask_ids:
            return
        await asyncio.gather(
            *(self._dispatch_subtask(subtask_id) for subtask_id in subtask_ids),
            return_exceptions=True,
        )
        await self.aggregate_task(task_id)

    async def _dispatch_subtask(self, subtask_id: str) -> None:
        lock = self._subtask_locks.setdefault(subtask_id, asyncio.Lock())
        async with lock:
            async with async_session() as db:
                result = await db.execute(
                    select(Subtask, Task)
                    .join(Task, Task.id == Subtask.task_id)
                    .where(Subtask.id == subtask_id)
                )
                row = result.one_or_none()
                if row is None:
                    return
                subtask, task = row
                if subtask.status != "queued":
                    return
                if subtask.node_id not in manager.get_connected_agents():
                    subtask.status = "paused"
                    subtask.error = "Agent offline"
                    subtask.message = "节点离线，等待手动重试"
                    task.updated = utcnow()
                    await db.commit()
                    return
                if not subtask.execution_id:
                    subtask.execution_id = str(uuid.uuid4())
                execution_id = subtask.execution_id
                subtask.attempts += 1
                subtask.error = None
                subtask.status = "running"
                subtask.started = subtask.started or utcnow()
                subtask.message = "等待 Agent 确认"
                params = _safe_json(task.params, {})
                task_id = task.id
                task_type = task.type
                node_id = subtask.node_id
                if task.started is None:
                    task.started = utcnow()
                task.status = "running"
                task.updated = utcnow()
                await db.commit()

            try:
                acknowledgement = await manager.request_agent(
                    node_id,
                    {
                        "type": "task",
                        "request_id": execution_id,
                        "payload": {
                            "task_id": task_id,
                            "subtask_id": subtask_id,
                            "task_type": task_type,
                            "params": params,
                        },
                    },
                    timeout=settings.TASK_ACK_TIMEOUT_SECONDS,
                )
            except (asyncio.TimeoutError, ConnectionError):
                await self._set_dispatch_error(
                    subtask_id, execution_id, "paused", "Agent acknowledgement timed out",
                )
                return
            except Exception:
                await self._set_dispatch_error(
                    subtask_id, execution_id, "paused", "Task dispatch failed",
                )
                return

            if acknowledgement.get("accepted") is not True:
                error = str(acknowledgement.get("error") or "Agent rejected task")[:1000]
                await self._set_dispatch_error(subtask_id, execution_id, "failed", error)
                return
            async with async_session() as db:
                await db.execute(
                    update(Subtask)
                    .where(
                        Subtask.id == subtask_id,
                        Subtask.execution_id == execution_id,
                        Subtask.status == "running",
                    )
                    .values(message="Agent 已确认，任务执行中")
                    .execution_options(synchronize_session=False)
                )
                await db.commit()
            await self.aggregate_task(task_id)

    async def _set_dispatch_error(
        self, subtask_id: str, execution_id: str, status: str, error: str,
    ) -> None:
        async with async_session() as db:
            task_id = await db.scalar(select(Subtask.task_id).where(Subtask.id == subtask_id))
            if task_id is None:
                return
            values = {
                "status": status,
                "error": error[:2000],
                "message": "等待手动重试" if status == "paused" else "任务被 Agent 拒绝",
            }
            if status == "failed":
                values["finished"] = utcnow()
            changed = await db.execute(
                update(Subtask)
                .where(
                    Subtask.id == subtask_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status.in_(["queued", "running"]),
                )
                .values(**values)
                .execution_options(synchronize_session=False)
            )
            await db.commit()
        if changed.rowcount:
            await self.aggregate_task(task_id)

    async def handle_progress(self, node_id: str, execution_id: str, payload: dict) -> None:
        try:
            progress = max(0, min(100, int(payload.get("progress", 0))))
        except (TypeError, ValueError):
            progress = 0
        message = str(payload.get("message") or "")[:500] or None
        async with async_session() as db:
            result = await db.execute(
                select(Subtask.task_id, Subtask.status, Task.created_by)
                .join(Task, Task.id == Subtask.task_id)
                .where(
                    Subtask.node_id == node_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status.in_(["queued", "running", "cancel_requested"]),
                )
            )
            row = result.one_or_none()
            if row is None:
                return
            task_id, previous_status, owner = row
            changed = await db.execute(
                update(Subtask)
                .where(
                    Subtask.node_id == node_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status.in_(["queued", "running", "cancel_requested"]),
                )
                .values(
                    status=case(
                        (Subtask.status == "queued", "running"),
                        else_=Subtask.status,
                    ),
                    started=func.coalesce(Subtask.started, utcnow()),
                    progress=progress,
                    message=message,
                )
                .execution_options(synchronize_session=False)
            )
            if not changed.rowcount:
                await db.rollback()
                return
            await db.execute(
                update(Task).where(Task.id == task_id).values(updated=utcnow())
            )
            await db.commit()
        current_status = "cancel_requested" if previous_status == "cancel_requested" else "running"
        await self._broadcast(task_id, owner, node_id, current_status, progress)

    async def handle_result(self, node_id: str, execution_id: str, payload: dict) -> None:
        status = str(payload.get("status") or "")
        if status == "cancelled":
            final_status = "cancelled"
        else:
            final_status = "completed" if payload.get("success") is True else "failed"
        safe_result = payload.get("result")
        if not isinstance(safe_result, dict):
            safe_result = {}
        encoded_result = json.dumps(safe_result, ensure_ascii=False)
        if len(encoded_result.encode("utf-8")) > 64 * 1024:
            safe_result = {"truncated": True, "message": "Agent result exceeded 64 KiB"}
            encoded_result = json.dumps(safe_result)
            final_status = "failed"
        error = str(payload.get("error") or "")[:2000] or None

        async with async_session() as db:
            result = await db.execute(
                select(Subtask.task_id, Subtask.progress, Task.created_by, Task.type)
                .join(Task, Task.id == Subtask.task_id)
                .where(
                    Subtask.node_id == node_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status.in_(["queued", "running", "paused", "cancel_requested"]),
                )
            )
            row = result.one_or_none()
            if row is None:
                return
            task_id, previous_progress, owner, task_type = row
            final_progress = (
                100 if final_status in {"completed", "cancelled"} else previous_progress
            )
            changed = await db.execute(
                update(Subtask)
                .where(
                    Subtask.node_id == node_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status.in_(["queued", "running", "paused", "cancel_requested"]),
                )
                .values(
                    status=final_status,
                    progress=final_progress,
                    result=encoded_result,
                    error=error if final_status == "failed" else None,
                    message=str(payload.get("message") or "")[:500] or final_status,
                    started=func.coalesce(Subtask.started, utcnow()),
                    finished=utcnow(),
                )
                .execution_options(synchronize_session=False)
            )
            if not changed.rowcount:
                await db.rollback()
                return
            await db.execute(
                update(Task).where(Task.id == task_id).values(updated=utcnow())
            )
            dangerous = task_type in {"restart_service", "batch_command"}
            if dangerous:
                db.add(AuditLog(
                    user_id=owner,
                    action="task.dangerous.result",
                    resource=task_id,
                    detail=json.dumps({
                        "type": task_type,
                        "node_id": node_id,
                        "status": final_status,
                    }),
                ))
            await db.commit()
        await self.aggregate_task(task_id)
        await self._broadcast(task_id, owner, node_id, final_status, final_progress)

    async def cancel_task(self, task_id: str) -> str:
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            async with async_session() as db:
                now = utcnow()
                await db.execute(
                    update(Subtask)
                    .where(
                        Subtask.task_id == task_id,
                        Subtask.status.in_(["queued", "paused"]),
                    )
                    .values(
                        status="cancelled",
                        progress=100,
                        finished=now,
                        message="未开始执行，已取消",
                    )
                    .execution_options(synchronize_session=False)
                )
                await db.execute(
                    update(Subtask)
                    .where(
                        Subtask.task_id == task_id,
                        Subtask.status == "running",
                        Subtask.execution_id.is_not(None),
                    )
                    .values(
                        status="cancel_requested",
                        message="等待 Agent 确认取消",
                    )
                    .execution_options(synchronize_session=False)
                )
                result = await db.execute(select(
                    Subtask.id, Subtask.node_id, Subtask.execution_id,
                ).where(
                    Subtask.task_id == task_id,
                    Subtask.status == "cancel_requested",
                    Subtask.execution_id.is_not(None),
                ))
                cancellable = [
                    (subtask_id, node_id, execution_id)
                    for subtask_id, node_id, execution_id in result.all()
                    if execution_id
                ]
                task_result = await db.execute(select(Task).where(Task.id == task_id))
                task = task_result.scalar_one_or_none()
                if task:
                    task.status = "cancel_requested" if cancellable else "cancelled"
                    task.updated = now
                    if not cancellable:
                        task.finished = now
                await db.commit()

            if cancellable:
                await asyncio.gather(
                    *(self._cancel_subtask(*item) for item in cancellable),
                    return_exceptions=True,
                )
            await self.aggregate_task(task_id)
            async with async_session() as db:
                current = await db.scalar(select(Task.status).where(Task.id == task_id))
                return str(current or "cancel_requested")

    async def _cancel_subtask(self, subtask_id: str, node_id: str, execution_id: str) -> None:
        lock = self._subtask_locks.setdefault(subtask_id, asyncio.Lock())
        async with lock:
            async with async_session() as db:
                still_pending = await db.scalar(select(Subtask.id).where(
                    Subtask.id == subtask_id,
                    Subtask.execution_id == execution_id,
                    Subtask.status == "cancel_requested",
                ))
            if not still_pending:
                return
            try:
                response = await manager.request_agent(
                    node_id,
                    {
                        "type": "task_cancel",
                        "request_id": execution_id,
                        "payload": {"execution_id": execution_id},
                    },
                    timeout=settings.TASK_CANCEL_TIMEOUT_SECONDS,
                )
            except Exception:
                return
            if response.get("cancelled") is not True:
                return
            async with async_session() as db:
                await db.execute(
                    update(Subtask)
                    .where(
                        Subtask.id == subtask_id,
                        Subtask.execution_id == execution_id,
                        Subtask.status == "cancel_requested",
                    )
                    .values(
                        status="cancelled",
                        progress=100,
                        message="Agent 已确认取消",
                        finished=utcnow(),
                    )
                    .execution_options(synchronize_session=False)
                )
                await db.commit()

    async def retry_task(self, task_id: str, target_node_ids: list[str] | None) -> list[str]:
        lock = self._task_locks.setdefault(task_id, asyncio.Lock())
        async with lock:
            async with async_session() as db:
                query = select(Subtask).where(
                    Subtask.task_id == task_id,
                    Subtask.status.in_(RETRYABLE_SUBTASK_STATUSES),
                )
                if target_node_ids is not None:
                    query = query.where(Subtask.node_id.in_(set(target_node_ids)))
                result = await db.execute(query)
                subtasks = list(result.scalars().all())
                selected = {item.node_id for item in subtasks}
                if target_node_ids is not None and selected != set(target_node_ids):
                    raise ValueError("只能重试该任务中 failed 或 paused 的节点")
                if not subtasks:
                    raise ValueError("没有可重试的节点")
                for subtask in subtasks:
                    subtask.status = "queued"
                    subtask.execution_id = str(uuid.uuid4())
                    subtask.progress = 0
                    subtask.message = "等待重新分发"
                    subtask.error = None
                    subtask.result = None
                    subtask.started = None
                    subtask.finished = None
                task_result = await db.execute(select(Task).where(Task.id == task_id))
                task = task_result.scalar_one()
                task.status = "queued"
                task.updated = utcnow()
                task.finished = None
                await db.commit()
            node_ids = sorted(selected)
            await self._dispatch_task_locked(task_id, node_ids)
            return node_ids

    async def pause_node_tasks(self, node_id: str) -> None:
        async with async_session() as db:
            result = await db.execute(select(Subtask).where(
                Subtask.node_id == node_id,
                Subtask.status.in_(["queued", "running"]),
            ))
            task_ids: set[str] = set()
            for subtask in result.scalars().all():
                subtask.status = "paused"
                subtask.error = "Agent disconnected"
                subtask.message = "节点断线，等待手动重试"
                task_ids.add(subtask.task_id)
            await db.commit()
        for task_id in task_ids:
            await self.aggregate_task(task_id)

    async def aggregate_task(self, task_id: str) -> None:
        async with async_session() as db:
            task = await db.scalar(select(Task).where(Task.id == task_id))
            if task is None:
                return
            result = await db.execute(select(Subtask).where(Subtask.task_id == task_id))
            subtasks = list(result.scalars().all())
            if not subtasks:
                return
            statuses = [item.status for item in subtasks]
            if "cancel_requested" in statuses:
                status = "cancel_requested"
            elif any(item in {"queued", "running"} for item in statuses):
                status = "running" if "running" in statuses else "queued"
            elif all(item == "completed" for item in statuses):
                status = "completed"
            elif all(item == "cancelled" for item in statuses):
                status = "cancelled"
            elif all(item == "failed" for item in statuses):
                status = "failed"
            elif "paused" in statuses and not any(
                item in {"completed", "cancelled"} for item in statuses
            ):
                status = "paused"
            else:
                status = "partial"
            task.status = status
            task.updated = utcnow()
            if status in {"completed", "partial", "failed", "cancelled"}:
                task.finished = task.finished or utcnow()
            else:
                task.finished = None
            await db.commit()

    async def validate_cleanup_preview(
        self,
        db: AsyncSession,
        *,
        creator_id: str,
        preview_task_id: str,
        profile: str,
        older_than_days: int,
        target_node_ids: set[str],
    ) -> None:
        preview = await db.scalar(select(Task).where(
            Task.id == preview_task_id,
            Task.created_by == creator_id,
            Task.type == "clean_logs",
            Task.status == "completed",
        ))
        if preview is None or preview.finished is None:
            raise ValueError("日志清理预览不存在或尚未成功完成")
        finished = preview.finished
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=timezone.utc)
        if finished < utcnow() - timedelta(minutes=settings.TASK_PREVIEW_TTL_MINUTES):
            raise ValueError("日志清理预览已超过 30 分钟有效期")
        preview_params = _safe_json(preview.params, {})
        if (
            preview_params.get("profile") != profile
            or preview_params.get("older_than_days") != older_than_days
            or preview_params.get("dry_run") is not True
        ):
            raise ValueError("日志清理参数与预览不一致")
        nodes = await db.execute(select(Subtask.node_id).where(Subtask.task_id == preview_task_id))
        if set(nodes.scalars().all()) != target_node_ids:
            raise ValueError("日志清理目标与预览不一致")

    async def cleanup_history(self) -> int:
        cutoff = utcnow() - timedelta(days=settings.TASK_RETENTION_DAYS)
        async with async_session() as db:
            task_result = await db.execute(select(Task.id).where(
                Task.status.in_(["completed", "partial", "failed", "cancelled"]),
                Task.finished < cutoff,
            ))
            task_ids = list(task_result.scalars().all())
            if not task_ids:
                return 0
            subtask_result = await db.execute(
                select(Subtask.id).where(Subtask.task_id.in_(task_ids))
            )
            subtask_ids = list(subtask_result.scalars().all())
            await db.execute(delete(Task).where(Task.id.in_(task_ids)))
            await db.commit()
        for subtask_id in subtask_ids:
            self._subtask_locks.pop(subtask_id, None)
        for task_id in task_ids:
            self._task_locks.pop(task_id, None)
        return len(task_ids)

    async def cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup_history()
            except Exception:
                logger.exception("Task history cleanup failed")
            await asyncio.sleep(24 * 60 * 60)

    async def _broadcast(
        self, task_id: str, owner: str | None, node_id: str, status: str, progress: int,
    ) -> None:
        await manager.broadcast_to_frontends(
            {
                "type": "task_progress",
                "payload": {
                    "task_id": task_id,
                    "node_id": node_id,
                    "status": status,
                    "progress": progress,
                },
            },
            allowed_roles={"admin", "operator"},
            allowed_user_ids={owner} if owner else set(),
            unrestricted_roles={"admin"},
        )


task_service = TaskService()
