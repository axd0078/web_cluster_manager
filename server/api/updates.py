from __future__ import annotations

import asyncio
import json
import os
import shutil
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.update_package import UpdatePackageError, verify_update_package
from database import get_db
from middleware.auth import require_permission
from models.node import (
    AuditLog,
    UpdateDeployment,
    UpdateDeploymentTarget,
    UpdatePackage,
)
from models.user import User
from schemas.update import UpdateDeploymentCreate, UpdateRetry, UpdateRollback, UpdateTargets
from services.update_service import (
    deployment_snapshot,
    normalize_arch,
    resolve_update_targets,
    update_package_path,
    update_service,
    utcnow,
)


router = APIRouter(prefix="/api/v2/updates", tags=["updates"])


def _audit(
    db: AsyncSession,
    request: Request,
    user: User,
    action: str,
    resource: str,
    detail: dict,
) -> None:
    db.add(AuditLog(
        user_id=user.id,
        action=action,
        resource=resource,
        detail=json.dumps(detail, ensure_ascii=False, sort_keys=True),
        ip=request.client.host if request.client else None,
    ))


def _serialize_package(package: UpdatePackage) -> dict:
    return {
        "id": package.id,
        "version": package.version,
        "release_id": package.release_id,
        "component": package.component,
        "description": package.description,
        "filename": package.filename,
        "size": package.size,
        "expanded_size": package.expanded_size,
        "sha256": package.sha256,
        "target_os": package.target_os,
        "target_arch": package.target_arch,
        "python_abi": package.python_abi,
        "key_id": package.key_id,
        "min_updater_version": package.min_updater_version,
        "validation_status": package.validation_status,
        "verification_status": package.validation_status,
        "validation_error": package.validation_error,
        "created_by": package.created_by,
        "created": package.created.isoformat() if package.created else None,
    }


def _safe_upload_filename(value: str | None) -> str:
    raw = value or ""
    if (
        not raw
        or len(raw) > 255
        or Path(raw).name != raw
        or "/" in raw
        or "\\" in raw
        or "\x00" in raw
        or not raw.lower().endswith(".wcmupd")
    ):
        raise HTTPException(status_code=400, detail="更新包文件名必须是普通 .wcmupd 文件")
    return raw


async def _stream_upload(file: UploadFile, destination: Path) -> int:
    total = 0
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as output:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > settings.MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="更新包超过允许大小")
                output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    if total == 0:
        destination.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="更新包不能为空")
    return total


@router.post("/packages", status_code=201)
async def create_package(
    request: Request,
    file: UploadFile,
    description_form: str = Form("", alias="description", max_length=2000),
    description: str = Query("", max_length=2000),
    version: str | None = Query(None, max_length=50),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    filename = _safe_upload_filename(file.filename)
    quarantine = settings.UPDATES_DIR / ".quarantine"
    temporary = quarantine / f"{uuid.uuid4()}.part"
    await _stream_upload(file, temporary)
    try:
        verified = await asyncio.to_thread(
            verify_update_package,
            temporary,
            settings.UPDATE_TRUSTED_KEYS_DIR,
            max_package_bytes=settings.MAX_UPLOAD_BYTES,
            max_expanded_bytes=settings.UPDATE_MAX_EXPANDED_BYTES,
            max_entries=settings.UPDATE_MAX_ENTRIES,
        )
        if version is not None and version != verified.version:
            raise UpdatePackageError("请求版本与签名清单版本不一致")
        existing = await db.scalar(select(UpdatePackage).where(
            UpdatePackage.release_id == verified.release_id
        ))
        if existing is not None:
            raise HTTPException(status_code=409, detail="release ID 已存在")
        package_id = str(uuid.uuid4())
        package_dir = settings.UPDATES_DIR / package_id
        package_dir.mkdir(parents=True, exist_ok=False)
        destination = package_dir / filename
        await asyncio.to_thread(os.replace, temporary, destination)
        package = UpdatePackage(
            id=package_id,
            version=verified.version,
            release_id=verified.release_id,
            component="agent",
            description=(description_form or description).strip() or None,
            filename=filename,
            size=verified.package_size,
            expanded_size=verified.expanded_size,
            sha256=verified.package_sha256,
            target_os=verified.target_os,
            target_arch=normalize_arch(verified.target_arch),
            python_abi=verified.python_abi,
            key_id=verified.key_id,
            min_updater_version=verified.min_updater_version,
            manifest=verified.manifest_json,
            validation_status="verified",
            created_by=user.id,
        )
        db.add(package)
        _audit(
            db, request, user, "update.package.upload", package.id,
            {
                "release_id": package.release_id,
                "version": package.version,
                "target": [package.target_os, package.target_arch, package.python_abi],
                "key_id": package.key_id,
                "size": package.size,
                "sha256": package.sha256,
            },
        )
        try:
            await db.commit()
        except IntegrityError as exc:
            await db.rollback()
            await asyncio.to_thread(shutil.rmtree, package_dir, True)
            raise HTTPException(status_code=409, detail="release ID 已存在") from exc
        return _serialize_package(package)
    except HTTPException:
        temporary.unlink(missing_ok=True)
        raise
    except UpdatePackageError as exc:
        temporary.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


@router.get("/packages")
async def list_packages(
    limit: int = Query(100, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("updates.read")),
):
    result = await db.execute(
        select(UpdatePackage).order_by(UpdatePackage.created.desc()).limit(limit)
    )
    return [_serialize_package(package) for package in result.scalars().all()]


@router.delete("/packages/{package_id}", status_code=204)
async def delete_package(
    package_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    package = await db.get(UpdatePackage, package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="更新包不存在")
    deployment = await db.scalar(select(UpdateDeployment.id).where(
        UpdateDeployment.package_id == package_id
    ).limit(1))
    if deployment:
        raise HTTPException(status_code=409, detail="更新包已有发布历史，不能删除")
    package_dir = (settings.UPDATES_DIR.resolve() / package.id).resolve()
    if settings.UPDATES_DIR.resolve() not in package_dir.parents:
        raise HTTPException(status_code=400, detail="更新包存储路径无效")
    _audit(
        db, request, user, "update.package.delete", package.id,
        {"release_id": package.release_id, "version": package.version},
    )
    await db.delete(package)
    await db.commit()
    await asyncio.to_thread(shutil.rmtree, package_dir, True)
    return Response(status_code=204)


@router.post("/targets/resolve")
async def resolve_targets(
    body: UpdateTargets,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("updates.read")),
):
    package = await db.get(UpdatePackage, body.package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="更新包不存在")
    if package.validation_status != "verified":
        raise HTTPException(status_code=409, detail="更新包未通过可信签名验证")
    try:
        _, resolved = await resolve_update_targets(
            db,
            package,
            target_node_ids=body.target_node_ids,
            target_group_ids=body.target_group_ids,
            all_online=body.all_online,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"package_id": package.id, "nodes": resolved}


@router.post("/deployments", status_code=202)
async def create_deployment(
    body: UpdateDeploymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    package = await db.get(UpdatePackage, body.package_id)
    if package is None:
        raise HTTPException(status_code=404, detail="更新包不存在")
    if package.validation_status != "verified" or not update_package_path(package).is_file():
        raise HTTPException(status_code=409, detail="可信更新包文件不可用")
    try:
        nodes, _ = await resolve_update_targets(
            db,
            package,
            target_node_ids=body.target_node_ids,
            target_group_ids=body.target_group_ids,
            all_online=body.all_online,
            require_compatible=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    node_ids = {node.id for node in nodes}
    canary_ids = set(body.canary_node_ids)
    if not canary_ids:
        raise HTTPException(status_code=422, detail="必须选择至少一个 canary 节点")
    if not canary_ids.issubset(node_ids):
        raise HTTPException(status_code=422, detail="canary 节点必须属于已解析目标")
    if len(nodes) > 1 and len(canary_ids) >= len(nodes):
        raise HTTPException(
            status_code=422,
            detail="多节点发布必须保留至少一个非 canary 节点等待人工批准",
        )
    deployment = UpdateDeployment(
        package_id=package.id,
        status="queued",
        kind="update",
        canary_node_ids=json.dumps(sorted(canary_ids)),
        created_by=user.id,
    )
    db.add(deployment)
    await db.flush()
    targets: list[UpdateDeploymentTarget] = []
    for node in nodes:
        try:
            capabilities = json.loads(node.capabilities or "{}")
        except json.JSONDecodeError:
            capabilities = {}
        target = UpdateDeploymentTarget(
            deployment_id=deployment.id,
            node_id=node.id,
            is_canary=node.id in canary_ids,
            status="queued",
            phase="queued",
            from_release_id=capabilities.get("active_release_id"),
            from_version=str(capabilities.get("active_version") or node.version or "") or None,
            to_release_id=package.release_id,
            to_version=package.version,
            message="等待 canary 发布" if node.id in canary_ids else "等待 canary 批准",
        )
        targets.append(target)
    db.add_all(targets)
    _audit(
        db, request, user, "update.deployment.create", deployment.id,
        {
            "release_id": package.release_id,
            "target_count": len(nodes),
            "canary_count": len(canary_ids),
        },
    )
    await db.commit()
    snapshot = await deployment_snapshot(db, deployment)
    update_service.start(deployment.id)
    return snapshot


@router.get("/deployments")
async def list_deployments(
    status: str | None = Query(None, max_length=30),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("updates.read")),
):
    query = select(UpdateDeployment).order_by(UpdateDeployment.created.desc()).limit(limit)
    if status:
        query = query.where(UpdateDeployment.status == status)
    result = await db.execute(query)
    return [await deployment_snapshot(db, deployment) for deployment in result.scalars().all()]


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    deployment_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("updates.read")),
):
    deployment = await db.get(UpdateDeployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    return await deployment_snapshot(db, deployment)


@router.post("/deployments/{deployment_id}/approve", status_code=202)
async def approve_deployment(
    deployment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    deployment = await db.get(UpdateDeployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if deployment.kind != "update" or deployment.status != "awaiting_approval":
        raise HTTPException(status_code=409, detail="当前发布任务不等待 canary 批准")
    deployment.approved_at = deployment.updated = utcnow()
    deployment.status = "rolling_out"
    _audit(db, request, user, "update.deployment.approve", deployment.id, {})
    await db.commit()
    update_service.start(deployment.id)
    return await deployment_snapshot(db, deployment)


@router.post("/deployments/{deployment_id}/cancel", status_code=202)
async def cancel_deployment(
    deployment_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    deployment = await db.get(UpdateDeployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if deployment.status in {"completed", "failed", "cancelled", "rolled_back"}:
        raise HTTPException(status_code=409, detail="发布任务已经结束")
    _audit(db, request, user, "update.deployment.cancel", deployment.id, {})
    await db.commit()
    await update_service.cancel(deployment.id)
    await db.refresh(deployment)
    return await deployment_snapshot(db, deployment)


@router.post("/deployments/{deployment_id}/retry", status_code=202)
async def retry_deployment(
    deployment_id: str,
    body: UpdateRetry,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    deployment = await db.get(UpdateDeployment, deployment_id)
    if deployment is None:
        raise HTTPException(status_code=404, detail="发布任务不存在")
    if update_service.is_running(deployment.id):
        raise HTTPException(status_code=409, detail="发布任务仍在运行")
    query = select(UpdateDeploymentTarget).where(
        UpdateDeploymentTarget.deployment_id == deployment.id,
        UpdateDeploymentTarget.status.in_(["failed", "paused", "rollback_failed"]),
    )
    if body.target_node_ids:
        query = query.where(UpdateDeploymentTarget.node_id.in_(body.target_node_ids))
    result = await db.execute(query)
    targets = list(result.scalars().all())
    if not targets:
        raise HTTPException(status_code=409, detail="没有可重试节点")
    found = {target.node_id for target in targets}
    if body.target_node_ids and found != set(body.target_node_ids):
        raise HTTPException(status_code=422, detail="只能选择该任务中失败或暂停的节点")
    for target in targets:
        target.status = target.phase = "queued"
        target.error = None
        target.message = "等待重试"
        target.finished = None
        target.updated = utcnow()
    deployment.cancel_requested = False
    deployment.status = "queued"
    deployment.finished = None
    deployment.updated = utcnow()
    _audit(
        db, request, user, "update.deployment.retry", deployment.id,
        {"target_node_ids": sorted(found)},
    )
    await db.commit()
    update_service.start(deployment.id, [target.id for target in targets])
    return await deployment_snapshot(db, deployment)


@router.post("/deployments/{deployment_id}/rollback", status_code=202)
async def rollback_deployment(
    deployment_id: str,
    body: UpdateRollback,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_permission("updates.manage")),
):
    source = await db.get(UpdateDeployment, deployment_id)
    if source is None:
        raise HTTPException(status_code=404, detail="原发布任务不存在")
    query = select(UpdateDeploymentTarget).where(
        UpdateDeploymentTarget.deployment_id == source.id,
        UpdateDeploymentTarget.status == "completed",
        UpdateDeploymentTarget.from_release_id.is_not(None),
        UpdateDeploymentTarget.from_version.is_not(None),
    )
    if body.target_node_ids:
        query = query.where(UpdateDeploymentTarget.node_id.in_(body.target_node_ids))
    result = await db.execute(query)
    source_targets = list(result.scalars().all())
    if not source_targets:
        raise HTTPException(status_code=409, detail="没有保留上一版本的成功节点可回滚")
    found = {target.node_id for target in source_targets}
    if body.target_node_ids and found != set(body.target_node_ids):
        raise HTTPException(status_code=422, detail="只能选择已成功且保留上一版本的节点")
    rollback = UpdateDeployment(
        package_id=source.package_id,
        source_deployment_id=source.id,
        kind="rollback",
        status="queued",
        canary_node_ids="[]",
        created_by=user.id,
    )
    db.add(rollback)
    await db.flush()
    rollback_targets = [
        UpdateDeploymentTarget(
            deployment_id=rollback.id,
            node_id=target.node_id,
            status="queued",
            phase="queued",
            from_release_id=target.to_release_id,
            from_version=target.to_version,
            to_release_id=target.from_release_id,
            to_version=target.from_version,
            message="等待回滚",
        )
        for target in source_targets
    ]
    db.add_all(rollback_targets)
    _audit(
        db, request, user, "update.deployment.rollback", rollback.id,
        {"source_deployment_id": source.id, "target_node_ids": sorted(found)},
    )
    await db.commit()
    snapshot = await deployment_snapshot(db, rollback)
    update_service.start(rollback.id)
    return snapshot


@router.post("/push", status_code=410)
async def legacy_push_removed(
    _user: User = Depends(require_permission("updates.manage")),
):
    raise HTTPException(
        status_code=410,
        detail="旧 Base64 更新接口已移除，请创建可信发布任务",
    )


@router.get("/nodes/{node_id}/version")
async def get_node_version(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _user: User = Depends(require_permission("cluster.read")),
):
    from models.node import Node

    node = await db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        capabilities = json.loads(node.capabilities or "{}")
    except json.JSONDecodeError:
        capabilities = {}
    return {
        "node_id": node.id,
        "version": node.version,
        "active_release_id": capabilities.get("active_release_id"),
        "updater_version": capabilities.get("updater_version"),
        "update_protocol": capabilities.get("update_protocol", 0),
        "status": node.status,
    }
