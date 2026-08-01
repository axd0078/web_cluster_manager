from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.connection_manager import manager
from database import async_session
from models.node import (
    AuditLog,
    Group,
    GroupNode,
    Node,
    UpdateAttempt,
    UpdateDeployment,
    UpdateDeploymentTarget,
    UpdatePackage,
)


logger = logging.getLogger("server.update")

RETRYABLE_TARGET_STATUSES = {"failed", "paused"}
ACTIVE_TARGET_STATUSES = {
    "transferring", "verified", "activating", "health_check", "rollback_running",
}
FINAL_TARGET_STATUSES = {
    "completed", "failed", "paused", "cancelled", "rolled_back", "rollback_failed",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_json(raw: str | None, fallback):
    try:
        value = json.loads(raw or "")
    except (TypeError, json.JSONDecodeError):
        return fallback
    return value if isinstance(value, type(fallback)) else fallback


def normalize_arch(value: object) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"amd64", "x86_64"}:
        return "x86_64"
    if normalized in {"arm64", "aarch64"}:
        return "aarch64"
    return normalized


def _version_tuple(value: object) -> tuple[int, int, int] | None:
    raw = str(value or "").split("+", 1)[0].split("-", 1)[0]
    pieces = raw.split(".")
    if len(pieces) != 3 or any(not piece.isdigit() for piece in pieces):
        return None
    return tuple(int(piece) for piece in pieces)  # type: ignore[return-value]


def update_capabilities(node: Node, package: UpdatePackage) -> tuple[bool, str | None, dict]:
    capabilities = _safe_json(node.capabilities, {})
    if capabilities.get("update_protocol") != 2:
        return False, "Agent 尚未安装 update v2 更新助手", capabilities
    updater_version = _version_tuple(capabilities.get("updater_version"))
    required_version = _version_tuple(package.min_updater_version)
    if updater_version is None or required_version is None or updater_version < required_version:
        return False, "Agent 更新助手版本低于发布包要求", capabilities
    node_os = str(capabilities.get("os_family") or "").lower()
    node_arch = normalize_arch(capabilities.get("architecture"))
    package_arch = normalize_arch(package.target_arch)
    python_abi = str(capabilities.get("python_abi") or "").lower()
    if node_os != str(package.target_os or "").lower():
        return False, "目标操作系统不匹配", capabilities
    if node_arch != package_arch:
        return False, "目标 CPU 架构不匹配", capabilities
    if python_abi != str(package.python_abi or "").lower():
        return False, "目标 Python ABI 不匹配", capabilities
    if capabilities.get("active_release_id") == package.release_id:
        return False, "Agent 已运行该 release", capabilities
    return True, None, capabilities


async def resolve_update_targets(
    db: AsyncSession,
    package: UpdatePackage,
    *,
    target_node_ids: list[str],
    target_group_ids: list[str],
    all_online: bool,
    require_compatible: bool = False,
) -> tuple[list[Node], list[dict]]:
    node_ids = set(target_node_ids)
    if target_group_ids:
        requested_groups = set(target_group_ids)
        result = await db.execute(select(Group.id).where(Group.id.in_(requested_groups)))
        found = set(result.scalars().all())
        if found != requested_groups:
            raise ValueError("一个或多个目标分组不存在")
        members = await db.execute(
            select(GroupNode.node_id).where(GroupNode.group_id.in_(found))
        )
        node_ids.update(members.scalars().all())
    if all_online:
        online = await db.execute(select(Node.id).where(Node.status == "online"))
        node_ids.update(online.scalars().all())
    if not node_ids:
        raise ValueError("至少需要解析出一个目标节点")
    if len(node_ids) > settings.UPDATE_MAX_TARGETS:
        raise ValueError(f"更新目标不能超过 {settings.UPDATE_MAX_TARGETS} 个 Agent")
    result = await db.execute(select(Node).where(Node.id.in_(node_ids)).order_by(Node.ip))
    nodes = list(result.scalars().all())
    if len(nodes) != len(node_ids):
        raise ValueError("一个或多个目标节点不存在")
    resolved: list[dict] = []
    incompatible = False
    for node in nodes:
        compatible, reason, caps = update_capabilities(node, package)
        incompatible = incompatible or not compatible
        resolved.append({
            "id": node.id,
            "hostname": node.hostname,
            "ip": node.ip,
            "platform": node.platform,
            "status": node.status,
            "is_online": node.status == "online",
            "version": node.version,
            "active_release_id": caps.get("active_release_id"),
            "compatible": compatible,
            "incompatibility": reason,
            "reasons": [] if reason is None else [reason],
        })
    if require_compatible and incompatible:
        reasons = sorted({item["incompatibility"] for item in resolved if item["incompatibility"]})
        raise ValueError("目标中存在不兼容 Agent：" + "；".join(reasons))
    return nodes, resolved


def update_package_path(package: UpdatePackage) -> Path:
    if not package.filename:
        raise ValueError("更新包没有可用文件")
    root = settings.UPDATES_DIR.resolve()
    package_dir = (root / package.id).resolve()
    path = (package_dir / package.filename).resolve()
    if root not in package_dir.parents or package_dir not in path.parents:
        raise ValueError("更新包存储路径无效")
    return path


async def deployment_snapshot(db: AsyncSession, deployment: UpdateDeployment) -> dict:
    result = await db.execute(
        select(UpdateDeploymentTarget)
        .where(UpdateDeploymentTarget.deployment_id == deployment.id)
        .order_by(UpdateDeploymentTarget.is_canary.desc(), UpdateDeploymentTarget.node_id)
    )
    targets = list(result.scalars().all())
    package = await db.get(UpdatePackage, deployment.package_id)
    progress = sum(max(0, min(100, target.progress)) for target in targets) // len(targets) if targets else 0
    return {
        "id": deployment.id,
        "package_id": deployment.package_id,
        "package": None if package is None else {
            "version": package.version,
            "release_id": package.release_id,
            "target_os": package.target_os,
            "target_arch": package.target_arch,
            "python_abi": package.python_abi,
        },
        "source_deployment_id": deployment.source_deployment_id,
        "kind": deployment.kind,
        "status": deployment.status,
        "progress": progress,
        "canary_node_ids": _safe_json(deployment.canary_node_ids, []),
        "cancel_requested": bool(deployment.cancel_requested),
        "created_by": deployment.created_by,
        "created": deployment.created.isoformat() if deployment.created else None,
        "started": deployment.started.isoformat() if deployment.started else None,
        "updated": deployment.updated.isoformat() if deployment.updated else None,
        "approved_at": deployment.approved_at.isoformat() if deployment.approved_at else None,
        "finished": deployment.finished.isoformat() if deployment.finished else None,
        "completed": deployment.finished.isoformat() if deployment.finished else None,
        "targets": [
            {
                "id": target.id,
                "node_id": target.node_id,
                "is_canary": bool(target.is_canary),
                "status": target.status,
                "phase": target.phase,
                "progress": target.progress,
                "bytes_sent": target.bytes_sent,
                "attempts": target.attempts,
                "execution_id": target.execution_id,
                "from_release_id": target.from_release_id,
                "from_version": target.from_version,
                "to_release_id": target.to_release_id,
                "to_version": target.to_version,
                "message": target.message,
                "error": target.error,
                "started": target.started.isoformat() if target.started else None,
                "updated": target.updated.isoformat() if target.updated else None,
                "finished": target.finished.isoformat() if target.finished else None,
                "completed": target.finished.isoformat() if target.finished else None,
            }
            for target in targets
        ],
    }


class AgentUpdateRejected(RuntimeError):
    pass


class UpdateService:
    def __init__(self) -> None:
        self._jobs: dict[str, asyncio.Task] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._health_waiters: dict[str, asyncio.Event] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.UPDATE_TRANSFER_CONCURRENCY))

    def start(self, deployment_id: str, target_ids: list[str] | None = None) -> bool:
        running = self._jobs.get(deployment_id)
        if running is not None and not running.done():
            return False
        task = asyncio.create_task(self._run_deployment(deployment_id, target_ids))
        self._jobs[deployment_id] = task
        task.add_done_callback(lambda completed: self._finished(deployment_id, completed))
        return True

    def is_running(self, deployment_id: str) -> bool:
        task = self._jobs.get(deployment_id)
        return task is not None and not task.done()

    def _finished(self, deployment_id: str, task: asyncio.Task) -> None:
        self._jobs.pop(deployment_id, None)
        self._locks.pop(deployment_id, None)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            logger.error(
                "Update deployment crashed: %s", deployment_id,
                exc_info=(type(error), error, error.__traceback__),
            )

    async def shutdown(self) -> None:
        jobs = list(self._jobs.values())
        for job in jobs:
            job.cancel()
        if jobs:
            await asyncio.gather(*jobs, return_exceptions=True)
        self._jobs.clear()
        await self.recover_after_restart()

    async def recover_after_restart(self) -> None:
        async with async_session() as db:
            result = await db.execute(
                select(UpdateDeploymentTarget).where(
                    UpdateDeploymentTarget.status.in_(ACTIVE_TARGET_STATUSES)
                )
            )
            deployment_ids: set[str] = set()
            for target in result.scalars().all():
                target.status = "paused"
                target.phase = "paused"
                target.error = target.error or "服务重启，等待管理员重试"
                target.updated = utcnow()
                deployment_ids.add(target.deployment_id)
            if deployment_ids:
                deployments = await db.execute(
                    select(UpdateDeployment).where(UpdateDeployment.id.in_(deployment_ids))
                )
                for deployment in deployments.scalars().all():
                    deployment.status = "paused"
                    deployment.updated = utcnow()
            await db.commit()

    async def pause_node(self, node_id: str) -> None:
        execution_ids: list[str] = []
        deployment_ids: set[str] = set()
        async with async_session() as db:
            result = await db.execute(
                select(UpdateDeploymentTarget).where(
                    UpdateDeploymentTarget.node_id == node_id,
                    UpdateDeploymentTarget.status.in_(ACTIVE_TARGET_STATUSES),
                )
            )
            for target in result.scalars().all():
                target.status = "paused"
                target.phase = "paused"
                target.error = "Agent 已断线，等待管理员重试"
                target.updated = utcnow()
                deployment_ids.add(target.deployment_id)
                if target.execution_id:
                    execution_ids.append(target.execution_id)
            for deployment_id in deployment_ids:
                deployment = await db.get(UpdateDeployment, deployment_id)
                if deployment:
                    deployment.status = "paused"
                    deployment.updated = utcnow()
            await db.commit()
        for execution_id in execution_ids:
            event = self._health_waiters.get(execution_id)
            if event:
                event.set()
        for deployment_id in deployment_ids:
            await self._broadcast(deployment_id)

    async def handle_health(self, node_id: str, payload: dict) -> tuple[bool, str]:
        execution_id = str(payload.get("execution_id") or "")
        release_id = str(payload.get("release_id") or "")
        version = str(payload.get("version") or "")
        if not execution_id or not release_id or not version:
            return False, "健康确认字段不完整"
        deployment_id: str | None = None
        async with async_session() as db:
            target = await db.scalar(select(UpdateDeploymentTarget).where(
                UpdateDeploymentTarget.execution_id == execution_id,
                UpdateDeploymentTarget.node_id == node_id,
            ))
            if target is None:
                return False, "更新尝试不存在或节点不匹配"
            if target.to_release_id != release_id or target.to_version != version:
                return False, "Agent 报告的活动版本与发布任务不一致"
            deployment = await db.get(UpdateDeployment, target.deployment_id)
            if deployment is None:
                return False, "发布任务不存在"
            target.status = "rolled_back" if deployment.kind == "rollback" else "completed"
            target.phase = target.status
            target.progress = 100
            target.message = "Agent 已健康重连并确认活动版本"
            target.error = None
            target.finished = target.updated = utcnow()
            attempt = await db.scalar(select(UpdateAttempt).where(
                UpdateAttempt.execution_id == execution_id
            ))
            if attempt:
                attempt.status = target.status
                attempt.finished = utcnow()
            node = await db.get(Node, node_id)
            if node:
                node.version = version
                capabilities = _safe_json(node.capabilities, {})
                capabilities["active_release_id"] = release_id
                capabilities["active_version"] = version
                node.capabilities = json.dumps(capabilities, sort_keys=True)
            deployment_id = deployment.id
            await db.commit()
        event = self._health_waiters.get(execution_id)
        if event:
            event.set()
        if deployment_id:
            await self._aggregate(deployment_id)
        return True, "健康确认已接受"

    async def handle_status(self, node_id: str, payload: dict) -> None:
        execution_id = str(payload.get("execution_id") or "")
        stage = str(payload.get("stage") or payload.get("status") or "")
        if not execution_id:
            return
        deployment_id: str | None = None
        async with async_session() as db:
            target = await db.scalar(select(UpdateDeploymentTarget).where(
                UpdateDeploymentTarget.execution_id == execution_id,
                UpdateDeploymentTarget.node_id == node_id,
            ))
            if target is None:
                return
            deployment = await db.get(UpdateDeployment, target.deployment_id)
            if deployment is None:
                return
            progress = payload.get("progress")
            if isinstance(progress, int) and not isinstance(progress, bool):
                target.progress = max(target.progress, min(100, max(0, progress)))
            if stage in {
                "verifying", "installing", "activation_queued", "activating",
                "rollback_queued", "rolling_back",
            }:
                target.phase = stage
                target.message = str(payload.get("message") or stage)[:500]
                target.updated = utcnow()
            elif stage == "completed":
                target.status = target.phase = "completed"
                target.progress = 100
                target.error = None
                target.finished = target.updated = utcnow()
            elif stage == "rolled_back":
                if deployment.kind == "rollback":
                    target.status = target.phase = "rolled_back"
                    target.progress = 100
                    target.error = None
                else:
                    target.status = "failed"
                    target.phase = "rolled_back"
                    target.error = str(
                        payload.get("error") or "新版本未通过健康检查，Agent 已自动回滚"
                    )[:2000]
                target.finished = target.updated = utcnow()
            elif stage in {"failed", "rollback_failed"}:
                target.status = (
                    "rollback_failed" if deployment.kind == "rollback" else "failed"
                )
                target.phase = stage
                target.error = str(payload.get("error") or "Agent 更新助手执行失败")[:2000]
                target.finished = target.updated = utcnow()
            elif stage == "cancelled":
                target.status = target.phase = "cancelled"
                target.finished = target.updated = utcnow()
            else:
                return
            deployment_id = target.deployment_id
            await db.commit()
        event = self._health_waiters.get(execution_id)
        if event:
            event.set()
        if deployment_id:
            await self._aggregate(deployment_id)

    async def cancel(self, deployment_id: str) -> str:
        active: list[tuple[str, str, str]] = []
        async with async_session() as db:
            deployment = await db.get(UpdateDeployment, deployment_id)
            if deployment is None:
                raise ValueError("发布任务不存在")
            deployment.cancel_requested = True
            deployment.status = "cancel_requested"
            deployment.updated = utcnow()
            result = await db.execute(select(UpdateDeploymentTarget).where(
                UpdateDeploymentTarget.deployment_id == deployment_id
            ))
            for target in result.scalars().all():
                if target.status in {"queued", "paused", "failed"}:
                    target.status = target.phase = "cancelled"
                    target.finished = target.updated = utcnow()
                elif target.status in {"transferring", "verified"} and target.execution_id:
                    active.append((target.node_id, target.execution_id, target.id))
            await db.commit()
        for node_id, execution_id, transfer_id in active:
            try:
                await manager.request_agent(node_id, {
                    "type": "update_cancel",
                    "payload": {
                        "execution_id": execution_id,
                        "transfer_id": transfer_id,
                    },
                }, timeout=max(1, settings.UPDATE_TRANSFER_TIMEOUT_SECONDS))
            except Exception:
                pass
        await self._aggregate(deployment_id)
        return "cancel_requested" if active else "cancelled"

    async def _run_deployment(
        self, deployment_id: str, selected_target_ids: list[str] | None,
    ) -> None:
        lock = self._locks.setdefault(deployment_id, asyncio.Lock())
        async with lock:
            async with async_session() as db:
                deployment = await db.get(UpdateDeployment, deployment_id)
                if deployment is None or deployment.cancel_requested:
                    return
                package = await db.get(UpdatePackage, deployment.package_id)
                if package is None:
                    return
                query = select(UpdateDeploymentTarget).where(
                    UpdateDeploymentTarget.deployment_id == deployment_id,
                    UpdateDeploymentTarget.status == "queued",
                )
                if selected_target_ids:
                    query = query.where(UpdateDeploymentTarget.id.in_(selected_target_ids))
                elif deployment.kind == "update" and deployment.approved_at is None:
                    query = query.where(UpdateDeploymentTarget.is_canary.is_(True))
                elif deployment.kind == "update":
                    query = query.where(UpdateDeploymentTarget.is_canary.is_(False))
                result = await db.execute(query.order_by(UpdateDeploymentTarget.node_id))
                target_ids = [target.id for target in result.scalars().all()]
                if not target_ids:
                    await self._aggregate(deployment_id)
                    return
                deployment.started = deployment.started or utcnow()
                deployment.updated = utcnow()
                if deployment.kind == "rollback":
                    deployment.status = "rollback_running"
                elif deployment.approved_at is None:
                    deployment.status = "canary_running"
                else:
                    deployment.status = "rolling_out"
                await db.commit()
            await self._broadcast(deployment_id)

            batch_size = max(1, settings.UPDATE_TRANSFER_CONCURRENCY)
            for index in range(0, len(target_ids), batch_size):
                async with async_session() as db:
                    deployment = await db.get(UpdateDeployment, deployment_id)
                    if deployment is None or deployment.cancel_requested:
                        break
                batch = target_ids[index:index + batch_size]
                await asyncio.gather(*[
                    self._run_target(deployment_id, target_id) for target_id in batch
                ])
                async with async_session() as db:
                    result = await db.execute(select(UpdateDeploymentTarget).where(
                        UpdateDeploymentTarget.id.in_(batch)
                    ))
                    expected = "rolled_back" if deployment.kind == "rollback" else "completed"
                    if any(target.status != expected for target in result.scalars().all()):
                        remaining = target_ids[index + batch_size:]
                        if remaining:
                            pending = await db.execute(select(UpdateDeploymentTarget).where(
                                UpdateDeploymentTarget.id.in_(remaining),
                                UpdateDeploymentTarget.status == "queued",
                            ))
                            for target in pending.scalars().all():
                                target.status = target.phase = "paused"
                                target.error = "前一发布批次失败，已停止扩大范围"
                                target.updated = utcnow()
                            await db.commit()
                        break
            await self._aggregate(deployment_id)

    async def _run_target(self, deployment_id: str, target_id: str) -> None:
        async with self._semaphore:
            async with async_session() as db:
                deployment = await db.get(UpdateDeployment, deployment_id)
                target = await db.get(UpdateDeploymentTarget, target_id)
                package = await db.get(UpdatePackage, deployment.package_id) if deployment else None
                if deployment is None or target is None or package is None:
                    return
                if deployment.cancel_requested or target.status != "queued":
                    return
                execution_id = str(uuid.uuid4())
                target.execution_id = execution_id
                target.attempts += 1
                target.status = target.phase = (
                    "rollback_running" if deployment.kind == "rollback" else "transferring"
                )
                target.error = None
                target.message = "开始回滚" if deployment.kind == "rollback" else "开始传输签名包"
                target.started = target.started or utcnow()
                target.updated = utcnow()
                attempt = UpdateAttempt(
                    target_id=target.id,
                    execution_id=execution_id,
                    attempt=target.attempts,
                    status="running",
                )
                db.add(attempt)
                node_id = target.node_id
                kind = deployment.kind
                await db.commit()
            await self._broadcast(deployment_id)
            waiter = asyncio.Event()
            self._health_waiters[execution_id] = waiter
            try:
                if kind == "rollback":
                    await self._request_rollback(node_id, execution_id, deployment_id, target_id)
                else:
                    await self._transfer_and_activate(
                        node_id, execution_id, deployment_id, target_id, package,
                    )
                try:
                    await asyncio.wait_for(
                        waiter.wait(), timeout=max(10, settings.UPDATE_HEALTH_TIMEOUT_SECONDS + 15),
                    )
                except asyncio.TimeoutError as exc:
                    raise AgentUpdateRejected(
                        "Agent 未在健康检查时限内确认新版本，更新助手将自动回滚"
                    ) from exc
                async with async_session() as db:
                    refreshed = await db.get(UpdateDeploymentTarget, target_id)
                    if refreshed and refreshed.status in {"completed", "rolled_back"}:
                        return
                    if refreshed and refreshed.status in {"failed", "paused", "rollback_failed"}:
                        return
                    raise AgentUpdateRejected("Agent 未返回有效的更新健康状态")
            except (ConnectionError, TimeoutError, asyncio.TimeoutError):
                await self._fail_target(target_id, "paused", "Agent 离线或响应超时")
            except AgentUpdateRejected as exc:
                status = "rollback_failed" if kind == "rollback" else "failed"
                await self._fail_target(target_id, status, str(exc))
            except Exception as exc:
                logger.error(
                    "Update target failed: %s (%s)",
                    target_id,
                    type(exc).__name__,
                )
                status = "rollback_failed" if kind == "rollback" else "failed"
                await self._fail_target(target_id, status, "更新执行发生内部错误")
            finally:
                self._health_waiters.pop(execution_id, None)
                await self._broadcast(deployment_id)

    async def _transfer_and_activate(
        self,
        node_id: str,
        execution_id: str,
        deployment_id: str,
        target_id: str,
        package: UpdatePackage,
    ) -> None:
        path = update_package_path(package)
        if not path.is_file() or path.stat().st_size != int(package.size or -1):
            raise AgentUpdateRejected("服务端更新包不存在或大小已变化")
        response = await self._request_with_retry(node_id, "update_transfer_init", {
            "execution_id": execution_id,
            "transfer_id": target_id,
            "package_id": package.id,
            "release_id": package.release_id,
            "version": package.version,
            "size": package.size,
            "sha256": package.sha256,
            "chunk_size": settings.UPDATE_CHUNK_BYTES,
        })
        if not response.get("success"):
            raise AgentUpdateRejected(str(response.get("error") or "Agent 拒绝更新传输"))
        offset = int(response.get("next_offset", 0))
        total_size = int(package.size or 0)
        if offset < 0 or offset > total_size:
            raise AgentUpdateRejected("Agent 返回了非法更新续传偏移")
        with path.open("rb") as source:
            source.seek(offset)
            while offset < total_size:
                chunk = source.read(settings.UPDATE_CHUNK_BYTES)
                if not chunk:
                    raise AgentUpdateRejected("服务端更新包读取提前结束")
                response = await self._request_with_retry(node_id, "update_transfer_chunk", {
                    "execution_id": execution_id,
                    "transfer_id": target_id,
                    "offset": offset,
                    "content_b64": base64.b64encode(chunk).decode("ascii"),
                    "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
                })
                if not response.get("success"):
                    raise AgentUpdateRejected(str(response.get("error") or "Agent 拒绝更新分块"))
                next_offset = int(response.get("next_offset", -1))
                if next_offset != offset + len(chunk):
                    raise AgentUpdateRejected("Agent 更新续传偏移不一致")
                offset = next_offset
                await self._target_progress(target_id, offset, total_size)
        response = await self._request_with_retry(node_id, "update_transfer_commit", {
            "execution_id": execution_id,
            "transfer_id": target_id,
            "sha256": package.sha256,
        })
        if not response.get("success"):
            raise AgentUpdateRejected(str(response.get("error") or "Agent 更新包校验失败"))
        await self._target_phase(target_id, "verified", "签名包已在 Agent 验签")
        response = await self._request_with_retry(node_id, "update_activate", {
            "execution_id": execution_id,
            "deployment_id": deployment_id,
            "release_id": package.release_id,
            "version": package.version,
            "package_sha256": package.sha256,
            "health_timeout": settings.UPDATE_HEALTH_TIMEOUT_SECONDS,
        })
        if not response.get("success") or response.get("accepted") is False:
            raise AgentUpdateRejected(str(response.get("error") or "Agent 拒绝激活更新"))
        await self._target_phase(target_id, "health_check", "等待新版本健康重连")

    async def _request_rollback(
        self, node_id: str, execution_id: str, deployment_id: str, target_id: str,
    ) -> None:
        async with async_session() as db:
            target = await db.get(UpdateDeploymentTarget, target_id)
            if target is None or not target.to_release_id or not target.to_version:
                raise AgentUpdateRejected("没有可回滚的上一版本")
            release_id = target.to_release_id
            version = target.to_version
        response = await self._request_with_retry(node_id, "update_rollback", {
            "execution_id": execution_id,
            "deployment_id": deployment_id,
            "target_release_id": release_id,
            "target_version": version,
            "health_timeout": settings.UPDATE_HEALTH_TIMEOUT_SECONDS,
        })
        if not response.get("success") or response.get("accepted") is False:
            raise AgentUpdateRejected(str(response.get("error") or "Agent 拒绝回滚"))
        await self._target_phase(target_id, "health_check", "等待回滚版本健康重连")

    async def _request_with_retry(self, node_id: str, message_type: str, payload: dict) -> dict:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                return await manager.request_agent(node_id, {
                    "type": message_type,
                    "request_id": f"update:{message_type}:{uuid.uuid4()}",
                    "payload": payload,
                }, timeout=max(1, settings.UPDATE_TRANSFER_TIMEOUT_SECONDS))
            except (ConnectionError, TimeoutError, asyncio.TimeoutError) as exc:
                last_error = exc
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)
        raise last_error or TimeoutError("Agent 未响应")

    async def _target_progress(self, target_id: str, sent: int, total: int) -> None:
        async with async_session() as db:
            target = await db.get(UpdateDeploymentTarget, target_id)
            if target:
                target.bytes_sent = sent
                target.progress = min(80, int(sent / total * 80)) if total else 0
                target.updated = utcnow()
                attempt = await db.scalar(select(UpdateAttempt).where(
                    UpdateAttempt.execution_id == target.execution_id
                ))
                if attempt:
                    attempt.bytes_sent = sent
                await db.commit()

    async def _target_phase(self, target_id: str, phase: str, message: str) -> None:
        values = {"verified": 85, "health_check": 95}
        async with async_session() as db:
            target = await db.get(UpdateDeploymentTarget, target_id)
            if target:
                target.status = target.phase = phase
                target.progress = values.get(phase, target.progress)
                target.message = message
                target.updated = utcnow()
                await db.commit()
        await self._broadcast_target(target_id)

    async def _fail_target(self, target_id: str, status: str, error: str) -> None:
        async with async_session() as db:
            target = await db.get(UpdateDeploymentTarget, target_id)
            if target is None:
                return
            target.status = target.phase = status
            target.error = error[:2000]
            target.finished = target.updated = utcnow()
            attempt = await db.scalar(select(UpdateAttempt).where(
                UpdateAttempt.execution_id == target.execution_id
            ))
            if attempt:
                attempt.status = status
                attempt.error = error[:2000]
                attempt.finished = utcnow()
            await db.commit()

    async def _aggregate(self, deployment_id: str) -> None:
        async with async_session() as db:
            deployment = await db.get(UpdateDeployment, deployment_id)
            if deployment is None:
                return
            result = await db.execute(select(UpdateDeploymentTarget).where(
                UpdateDeploymentTarget.deployment_id == deployment_id
            ))
            targets = list(result.scalars().all())
            statuses = [target.status for target in targets]
            now = utcnow()
            if deployment.cancel_requested and all(status in FINAL_TARGET_STATUSES for status in statuses):
                deployment.status = "cancelled"
                deployment.finished = now
            elif deployment.cancel_requested:
                deployment.status = "cancel_requested"
            elif deployment.kind == "rollback":
                if statuses and all(status == "rolled_back" for status in statuses):
                    deployment.status = "rolled_back"
                    deployment.finished = now
                elif any(status == "rollback_failed" for status in statuses):
                    deployment.status = "partial" if any(status == "rolled_back" for status in statuses) else "failed"
                    deployment.finished = now
                elif any(status in {"paused", "failed"} for status in statuses):
                    deployment.status = "paused"
            else:
                canaries = [target for target in targets if target.is_canary]
                remaining = [target for target in targets if not target.is_canary]
                if statuses and all(status == "completed" for status in statuses):
                    deployment.status = "completed"
                    deployment.finished = now
                elif (
                    deployment.approved_at is None
                    and canaries
                    and all(target.status == "completed" for target in canaries)
                    and remaining
                ):
                    deployment.status = "awaiting_approval"
                elif any(status in {"failed", "paused", "rollback_failed"} for status in statuses):
                    deployment.status = "paused"
                elif any(status in ACTIVE_TARGET_STATUSES for status in statuses):
                    deployment.status = "canary_running" if deployment.approved_at is None else "rolling_out"
            deployment.updated = now
            if deployment.status in {"completed", "failed", "partial", "cancelled", "rolled_back"}:
                db.add(AuditLog(
                    user_id=deployment.created_by,
                    action="update.deployment.finish",
                    resource=deployment.id,
                    detail=json.dumps({"status": deployment.status, "kind": deployment.kind}),
                ))
            await db.commit()
        await self._broadcast(deployment_id)

    async def _broadcast_target(self, target_id: str) -> None:
        async with async_session() as db:
            target = await db.get(UpdateDeploymentTarget, target_id)
            deployment_id = target.deployment_id if target else None
        if deployment_id:
            await self._broadcast(deployment_id)

    async def _broadcast(self, deployment_id: str) -> None:
        async with async_session() as db:
            deployment = await db.get(UpdateDeployment, deployment_id)
            if deployment is None:
                return
            payload = await deployment_snapshot(db, deployment)
        await manager.broadcast_to_frontends(
            {
                "type": "update_deployment_progress",
                "payload": {**payload, "deployment_id": deployment_id},
            },
            allowed_roles={"admin"},
        )


update_service = UpdateService()
