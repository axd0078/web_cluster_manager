from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import select

from config import settings
from core.connection_manager import manager
from database import async_session
from models.node import AuditLog, FileTransfer, FileTransferTarget, FileUpload

logger = logging.getLogger("server.file_transfer")

UPLOAD_DIR = settings.DATA_DIR / "uploads"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class AgentTransferRejected(RuntimeError):
    pass


class FileTransferService:
    def __init__(self) -> None:
        self._jobs: dict[str, asyncio.Task] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.FILE_TRANSFER_CONCURRENCY))

    def start(self, transfer_id: str, target_ids: list[str] | None = None) -> bool:
        running = self._jobs.get(transfer_id)
        if running is not None and not running.done():
            return False
        task = asyncio.create_task(self._run_transfer(transfer_id, target_ids))
        self._jobs[transfer_id] = task
        task.add_done_callback(lambda _task: self._jobs.pop(transfer_id, None))
        return True

    def is_running(self, transfer_id: str) -> bool:
        task = self._jobs.get(transfer_id)
        return task is not None and not task.done()

    async def recover_stale(self) -> None:
        """Turn interrupted in-process jobs into resumable database state."""
        async with async_session() as db:
            result = await db.execute(
                select(FileTransferTarget).where(
                    FileTransferTarget.status.in_(["queued", "running"])
                )
            )
            transfer_ids: set[str] = set()
            for target in result.scalars().all():
                target.status = "paused"
                target.error = "服务重启，等待人工续传"
                transfer_ids.add(target.transfer_id)
            if transfer_ids:
                transfers = await db.execute(
                    select(FileTransfer).where(FileTransfer.id.in_(transfer_ids))
                )
                for transfer in transfers.scalars().all():
                    transfer.status = "partial"
                    transfer.finished = _utcnow()
            await db.commit()

    async def cleanup_loop(self) -> None:
        while True:
            try:
                await self.cleanup_expired_uploads()
            except Exception:
                logger.exception("File upload cleanup failed")
            await asyncio.sleep(3600)

    async def cleanup_expired_uploads(self) -> None:
        partial_cutoff = _utcnow() - timedelta(
            hours=max(1, settings.FILE_UPLOAD_PARTIAL_TTL_HOURS)
        )
        ready_cutoff = _utcnow() - timedelta(
            hours=max(1, settings.FILE_UPLOAD_RETENTION_HOURS)
        )
        async with async_session() as db:
            result = await db.execute(
                select(FileUpload).where(FileUpload.status.in_(["uploading", "failed", "ready"]))
            )
            for upload in result.scalars().all():
                cutoff = ready_cutoff if upload.status == "ready" else partial_cutoff
                reference_time = upload.completed or upload.updated or upload.created
                if reference_time and reference_time >= cutoff:
                    continue
                if upload.status == "ready":
                    active = await db.scalar(
                        select(FileTransfer.id).where(
                            FileTransfer.source == upload.id,
                            FileTransfer.status.in_(["queued", "running"]),
                        ).limit(1)
                    )
                    if active:
                        continue
                for suffix in (".part", ".bin"):
                    path = UPLOAD_DIR / f"{upload.id}{suffix}"
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        logger.warning("Unable to remove expired upload %s", path)
                upload.status = "cancelled"
                upload.updated = _utcnow()
            await db.commit()

    async def _run_transfer(
        self, transfer_id: str, selected_target_ids: list[str] | None,
    ) -> None:
        try:
            async with async_session() as db:
                transfer = await db.get(FileTransfer, transfer_id)
                if transfer is None:
                    return
                upload = await db.get(FileUpload, transfer.source) if transfer.source else None
                query = select(FileTransferTarget).where(
                    FileTransferTarget.transfer_id == transfer_id,
                    FileTransferTarget.status.in_(["queued", "paused", "failed"]),
                )
                if selected_target_ids:
                    query = query.where(FileTransferTarget.id.in_(selected_target_ids))
                result = await db.execute(query)
                target_ids = [target.id for target in result.scalars().all()]
                if not target_ids:
                    return
                if upload is None or upload.status != "ready":
                    await self._fail_targets(
                        target_ids, "源文件不可用或已经过期",
                    )
                    await self._finish_transfer(transfer_id)
                    return
                source_path = UPLOAD_DIR / upload.stored_name
                if not source_path.is_file():
                    await self._fail_targets(target_ids, "服务端源文件不存在")
                    await self._finish_transfer(transfer_id)
                    return
                transfer.status = "running"
                transfer.started = transfer.started or _utcnow()
                transfer.finished = None
                await db.commit()

            await manager.broadcast_to_frontends({
                "type": "file_transfer_progress",
                "payload": {"transfer_id": transfer_id, "status": "running"},
            })
            await asyncio.gather(*[
                self._run_target(transfer_id, target_id, source_path)
                for target_id in target_ids
            ])
            await self._finish_transfer(transfer_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Unhandled file transfer failure: %s", transfer_id)
            async with async_session() as db:
                transfer = await db.get(FileTransfer, transfer_id)
                if transfer is not None:
                    transfer.status = "failed"
                    transfer.finished = _utcnow()
                    await db.commit()

    async def _run_target(
        self, transfer_id: str, target_row_id: str, source_path: Path,
    ) -> None:
        async with self._semaphore:
            async with async_session() as db:
                transfer = await db.get(FileTransfer, transfer_id)
                target = await db.get(FileTransferTarget, target_row_id)
                if transfer is None or target is None:
                    return
                target.status = "running"
                target.error = None
                target.attempts += 1
                target.started = target.started or _utcnow()
                target.finished = None
                node_id = target.node_id
                total_size = int(transfer.size or 0)
                await db.commit()

            await self._broadcast_target(transfer_id, target_row_id)
            try:
                init = await self._request_with_retry(node_id, {
                    "type": "file_transfer_init",
                    "payload": {
                        "transfer_id": target_row_id,
                        "filename": transfer.filename,
                        "dest_path": transfer.dest_path,
                        "size": total_size,
                        "sha256": transfer.sha256,
                        "chunk_size": settings.FILE_CHUNK_BYTES,
                        "overwrite": bool(transfer.overwrite),
                    },
                })
                if not init.get("success"):
                    raise AgentTransferRejected(
                        str(init.get("error") or "Agent 拒绝初始化文件")
                    )
                if init.get("completed"):
                    await self._set_target_status(
                        target_row_id, "completed", bytes_sent=total_size,
                    )
                    return
                offset = int(init.get("next_offset", 0))
                if offset < 0 or offset > total_size:
                    raise AgentTransferRejected("Agent 返回了非法续传偏移")

                with source_path.open("rb") as source:
                    source.seek(offset)
                    while offset < total_size:
                        chunk = source.read(settings.FILE_CHUNK_BYTES)
                        if not chunk:
                            raise OSError("读取服务端源文件时提前结束")
                        encoded = base64.b64encode(chunk).decode("ascii")
                        response = await self._request_with_retry(node_id, {
                            "type": "file_transfer_chunk",
                            "payload": {
                                "transfer_id": target_row_id,
                                "offset": offset,
                                "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                                "content_b64": encoded,
                            },
                        })
                        if not response.get("success"):
                            raise AgentTransferRejected(
                                str(response.get("error") or "Agent 拒绝文件分块")
                            )
                        expected = offset + len(chunk)
                        next_offset = int(response.get("next_offset", -1))
                        if next_offset != expected:
                            raise AgentTransferRejected(
                                f"Agent 续传偏移不一致：期望 {expected}，收到 {next_offset}"
                            )
                        offset = next_offset
                        await self._set_target_progress(target_row_id, offset)
                        await self._broadcast_target(transfer_id, target_row_id)

                committed = await self._request_with_retry(node_id, {
                    "type": "file_transfer_commit",
                    "payload": {
                        "transfer_id": target_row_id,
                        "sha256": transfer.sha256,
                    },
                })
                if not committed.get("success"):
                    raise AgentTransferRejected(
                        str(committed.get("error") or "Agent 文件校验失败")
                    )
                await self._set_target_status(
                    target_row_id, "completed", bytes_sent=total_size,
                )
            except (ConnectionError, TimeoutError, asyncio.TimeoutError) as exc:
                await self._set_target_status(
                    target_row_id, "paused", error=f"Agent 离线或响应超时：{exc}",
                )
            except AgentTransferRejected as exc:
                await self._set_target_status(target_row_id, "failed", error=str(exc))
            except Exception as exc:
                logger.exception("Target file transfer failed: %s", target_row_id)
                await self._set_target_status(target_row_id, "failed", error=str(exc))
            finally:
                await self._broadcast_target(transfer_id, target_row_id)

    async def _request_with_retry(self, node_id: str, message: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(3):
            message["request_id"] = (
                f"file:{message['type']}:{node_id}:{asyncio.get_running_loop().time()}:{attempt}"
            )
            try:
                return await manager.request_agent(
                    node_id, message,
                    timeout=max(1, settings.FILE_TRANSFER_TIMEOUT_SECONDS),
                )
            except (ConnectionError, TimeoutError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        raise last_error or TimeoutError("Agent 未响应")

    async def _set_target_progress(self, target_id: str, bytes_sent: int) -> None:
        async with async_session() as db:
            target = await db.get(FileTransferTarget, target_id)
            if target is not None:
                target.bytes_sent = bytes_sent
                await db.commit()

    async def _set_target_status(
        self, target_id: str, status: str, *,
        bytes_sent: int | None = None, error: str | None = None,
    ) -> None:
        async with async_session() as db:
            target = await db.get(FileTransferTarget, target_id)
            if target is None:
                return
            target.status = status
            target.error = error[:2000] if error else None
            if bytes_sent is not None:
                target.bytes_sent = bytes_sent
            if status in {"completed", "failed", "paused"}:
                target.finished = _utcnow()
            await db.commit()

    async def _fail_targets(self, target_ids: list[str], error: str) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(FileTransferTarget).where(FileTransferTarget.id.in_(target_ids))
            )
            for target in result.scalars().all():
                target.status = "failed"
                target.error = error
                target.finished = _utcnow()
            await db.commit()

    async def _finish_transfer(self, transfer_id: str) -> None:
        async with async_session() as db:
            transfer = await db.get(FileTransfer, transfer_id)
            if transfer is None:
                return
            result = await db.execute(
                select(FileTransferTarget).where(
                    FileTransferTarget.transfer_id == transfer_id
                )
            )
            statuses = [target.status for target in result.scalars().all()]
            if statuses and all(status == "completed" for status in statuses):
                transfer.status = "completed"
            elif any(status == "completed" for status in statuses) or any(
                status == "paused" for status in statuses
            ):
                transfer.status = "partial"
            else:
                transfer.status = "failed"
            transfer.finished = _utcnow()
            status = transfer.status
            db.add(AuditLog(
                user_id=transfer.created_by,
                action="file_transfer_finish",
                resource=f"transfer:{transfer.id}",
                detail=json.dumps({"status": status}, ensure_ascii=False),
            ))
            await db.commit()
        await manager.broadcast_to_frontends({
            "type": "file_transfer_progress",
            "payload": {"transfer_id": transfer_id, "status": status},
        })

    async def _broadcast_target(self, transfer_id: str, target_id: str) -> None:
        async with async_session() as db:
            target = await db.get(FileTransferTarget, target_id)
            if target is None:
                return
            payload = {
                "transfer_id": transfer_id,
                "target_id": target.id,
                "node_id": target.node_id,
                "status": target.status,
                "bytes_sent": target.bytes_sent,
                "error": target.error,
            }
        await manager.broadcast_to_frontends({
            "type": "file_transfer_progress", "payload": payload,
        })


file_transfer_service = FileTransferService()
