from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.connection_manager import manager
from database import async_session
from models.node import Subtask, Task


class TaskEngine:
    """Orchestrates task lifecycle: create → dispatch → track → complete."""

    @staticmethod
    async def handle_task_result(node_id: str, request_id: str, payload: dict):
        """Called when an agent sends back a task result."""
        async with async_session() as db:
            result = await db.execute(
                select(Subtask).where(
                    Subtask.task_id == request_id, Subtask.node_id == node_id
                )
            )
            st = result.scalar_one_or_none()
            if st is None:
                return

            now = datetime.now(timezone.utc)
            st.status = "completed" if payload.get("success") else "failed"
            st.result = json.dumps(payload)
            st.finished = now
            if st.started is None:
                st.started = now

            # Check if all subtasks are done
            task_result = await db.execute(select(Task).where(Task.id == request_id))
            task = task_result.scalar_one_or_none()
            if task:
                remaining = await db.execute(
                    select(Subtask).where(
                        Subtask.task_id == request_id,
                        Subtask.status.in_(["pending", "running"]),
                    )
                )
                if not remaining.scalars().all():
                    # All done — check success/failure
                    failed_count_result = await db.execute(
                        select(Subtask).where(
                            Subtask.task_id == request_id, Subtask.status == "failed"
                        )
                    )
                    failed_count = len(failed_count_result.scalars().all())
                    task.status = "failed" if failed_count > 0 else "completed"
                    task.finished = now

            await db.commit()

            # Broadcast progress to frontends
            subtask_count_result = await db.execute(
                select(Subtask).where(Subtask.task_id == request_id)
            )
            all_subtasks = subtask_count_result.scalars().all()
            completed = sum(1 for s in all_subtasks if s.status == "completed")
            failed = sum(1 for s in all_subtasks if s.status == "failed")

            await manager.broadcast_to_frontends(
                {
                    "type": "task_progress",
                    "payload": {
                        "task_id": request_id,
                        "node_id": node_id,
                        "status": st.status,
                        "progress": f"{completed + failed}/{len(all_subtasks)}",
                        "completed": completed,
                        "failed": failed,
                        "total": len(all_subtasks),
                    },
                },
                allowed_roles={"admin", "operator"},
            )

    @staticmethod
    async def check_stale_tasks():
        """Periodic task: mark tasks as failed if agents disconnect."""
        async with async_session() as db:
            result = await db.execute(
                select(Task).where(Task.status.in_(["pending", "running"]))
            )
            for task in result.scalars().all():
                subtask_result = await db.execute(
                    select(Subtask).where(Subtask.task_id == task.id)
                )
                all_done = True
                for st in subtask_result.scalars().all():
                    if st.status in ("pending", "running"):
                        all_done = False
                if all_done:
                    task.status = "completed"
                    task.finished = datetime.now(timezone.utc)
            await db.commit()


task_engine = TaskEngine()
