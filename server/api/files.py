from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import get_db
from middleware.auth import get_current_user, require_role
from models.node import (
    AuditLog, FileTransfer, FileTransferTarget, FileUpload, Node,
)
from models.user import User
from services.file_transfer_service import UPLOAD_DIR, file_transfer_service

router = APIRouter(prefix="/api/v2/files", tags=["files"])

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_filename(value: str) -> str:
    name = value.strip()
    if (
        not name or len(name) > 255 or "\x00" in name
        or "/" in name or "\\" in name or name in {".", ".."}
    ):
        raise ValueError("文件名必须是不含路径的普通文件名")
    return name


def validate_relative_destination(value: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 500 or "\x00" in raw:
        raise ValueError("目标路径不能为空或过长")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw.replace("\\", "/"))
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("目标路径必须是 Agent 沙箱内的相对路径")
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("目标路径不能包含空目录、. 或 ..")
    if any(":" in part for part in parts):
        raise ValueError("目标路径不能包含盘符或 NTFS 数据流")
    return "/".join(parts)


def _upload_path(upload_id: str, suffix: str) -> Path:
    try:
        normalized = str(uuid.UUID(upload_id))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="上传会话不存在") from exc
    root = UPLOAD_DIR.resolve()
    path = (root / f"{normalized}{suffix}").resolve()
    if root not in path.parents:
        raise HTTPException(status_code=400, detail="非法上传路径")
    return path


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


async def _get_upload_for_user(
    upload_id: str, user: User, db: AsyncSession,
) -> FileUpload:
    upload = await db.get(FileUpload, upload_id)
    if upload is None or (user.role != "admin" and upload.created_by != user.id):
        raise HTTPException(status_code=404, detail="上传会话不存在")
    return upload


def _serialize_upload(upload: FileUpload) -> dict:
    return {
        "id": upload.id,
        "filename": upload.original_filename,
        "size": upload.size,
        "sha256": upload.sha256,
        "received_bytes": upload.received_bytes,
        "chunk_size": settings.FILE_CHUNK_BYTES,
        "status": upload.status,
        "created": upload.created.isoformat() if upload.created else None,
        "updated": upload.updated.isoformat() if upload.updated else None,
        "completed": upload.completed.isoformat() if upload.completed else None,
    }


def _serialize_transfer(
    transfer: FileTransfer, targets: list[FileTransferTarget],
) -> dict:
    total = int(transfer.size or 0)
    return {
        "id": transfer.id,
        "upload_id": transfer.source,
        "filename": transfer.filename,
        "size": total,
        "sha256": transfer.sha256,
        "dest_path": transfer.dest_path,
        "overwrite": bool(transfer.overwrite),
        "status": transfer.status,
        "created_by": transfer.created_by,
        "created": transfer.created.isoformat() if transfer.created else None,
        "started": transfer.started.isoformat() if transfer.started else None,
        "finished": transfer.finished.isoformat() if transfer.finished else None,
        "targets": [
            {
                "id": target.id,
                "node_id": target.node_id,
                "status": target.status,
                "bytes_sent": target.bytes_sent,
                "progress": round((target.bytes_sent / total) * 100, 1) if total else 0,
                "attempts": target.attempts,
                "error": target.error,
                "started": target.started.isoformat() if target.started else None,
                "finished": target.finished.isoformat() if target.finished else None,
            }
            for target in targets
        ],
    }


def _add_audit(
    db: AsyncSession, request: Request, user: User,
    action: str, resource: str, detail: dict,
) -> None:
    db.add(AuditLog(
        user_id=user.id,
        action=action,
        resource=resource,
        detail=json.dumps(detail, ensure_ascii=False),
        ip=request.client.host if request.client else None,
    ))


class UploadCreate(BaseModel):
    filename: str
    size: int = Field(gt=0)
    sha256: str

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str) -> str:
        return _safe_filename(value)

    @field_validator("sha256")
    @classmethod
    def validate_sha256(cls, value: str) -> str:
        normalized = value.lower()
        if not SHA256_RE.fullmatch(normalized):
            raise ValueError("SHA-256 格式无效")
        return normalized


class TransferCreate(BaseModel):
    upload_id: str
    target_node_ids: list[str] = Field(min_length=1)
    dest_path: str
    overwrite: bool = False

    @field_validator("dest_path")
    @classmethod
    def validate_dest_path(cls, value: str) -> str:
        return validate_relative_destination(value)


@router.post("/uploads", status_code=201)
async def create_upload(
    body: UploadCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    if body.size > settings.MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过允许大小")
    existing_result = await db.execute(
        select(FileUpload).where(
            FileUpload.created_by == user.id,
            FileUpload.sha256 == body.sha256,
            FileUpload.size == body.size,
            FileUpload.status.in_(["uploading", "ready"]),
        ).order_by(FileUpload.created.desc())
    )
    existing = existing_result.scalars().first()
    if existing is not None:
        path = _upload_path(
            existing.id, ".bin" if existing.status == "ready" else ".part",
        )
        if path.exists():
            if existing.status == "uploading":
                actual_size = path.stat().st_size
                existing.received_bytes = min(actual_size, existing.size)
                existing.updated = _utcnow()
            return _serialize_upload(existing)

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    part_path = _upload_path(upload_id, ".part")
    try:
        await asyncio.to_thread(part_path.touch, 0o600, False)
    except FileExistsError:
        raise HTTPException(status_code=409, detail="上传会话冲突")
    upload = FileUpload(
        id=upload_id,
        original_filename=body.filename,
        stored_name=f"{upload_id}.bin",
        size=body.size,
        sha256=body.sha256,
        received_bytes=0,
        status="uploading",
        created_by=user.id,
    )
    db.add(upload)
    await db.flush()
    return _serialize_upload(upload)


@router.get("/uploads")
async def list_uploads(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(FileUpload).order_by(FileUpload.created.desc()).limit(limit)
    if user.role != "admin":
        query = query.where(FileUpload.created_by == user.id)
    if status:
        query = query.where(FileUpload.status == status)
    result = await db.execute(query)
    return [_serialize_upload(upload) for upload in result.scalars().all()]


@router.get("/uploads/{upload_id}")
async def get_upload(
    upload_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return _serialize_upload(await _get_upload_for_user(upload_id, user, db))


@router.put("/uploads/{upload_id}/chunks")
async def upload_chunk(
    upload_id: str,
    offset: int = Query(..., ge=0),
    chunk_sha256: str = Query(...),
    chunk: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    upload = await _get_upload_for_user(upload_id, user, db)
    if upload.status not in {"uploading", "failed"}:
        raise HTTPException(status_code=409, detail="上传会话已结束")
    normalized_hash = chunk_sha256.lower()
    if not SHA256_RE.fullmatch(normalized_hash):
        raise HTTPException(status_code=400, detail="分块 SHA-256 格式无效")
    part_path = _upload_path(upload.id, ".part")
    if not part_path.exists():
        raise HTTPException(status_code=409, detail="上传临时文件不存在，请重新创建会话")
    actual_offset = part_path.stat().st_size
    if offset != actual_offset:
        raise HTTPException(
            status_code=409,
            detail={"message": "分块偏移不一致", "expected_offset": actual_offset},
        )
    data = await chunk.read(settings.FILE_CHUNK_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="空分块无效")
    if len(data) > settings.FILE_CHUNK_BYTES:
        raise HTTPException(status_code=413, detail="分块超过允许大小")
    if actual_offset + len(data) > upload.size:
        raise HTTPException(status_code=413, detail="分块超过声明的文件大小")
    if hashlib.sha256(data).hexdigest() != normalized_hash:
        raise HTTPException(status_code=400, detail="分块 SHA-256 校验失败")

    def append_chunk() -> None:
        with part_path.open("ab") as destination:
            destination.write(data)
            destination.flush()
            os.fsync(destination.fileno())

    await asyncio.to_thread(append_chunk)
    upload.received_bytes = actual_offset + len(data)
    upload.status = "uploading"
    upload.updated = _utcnow()
    await db.flush()
    return {
        "upload_id": upload.id,
        "received_bytes": upload.received_bytes,
        "next_offset": upload.received_bytes,
        "size": upload.size,
        "progress": round(upload.received_bytes / upload.size * 100, 1),
    }


@router.post("/uploads/{upload_id}/complete")
async def complete_upload(
    upload_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    upload = await _get_upload_for_user(upload_id, user, db)
    if upload.status == "ready":
        return _serialize_upload(upload)
    part_path = _upload_path(upload.id, ".part")
    if not part_path.exists() or part_path.stat().st_size != upload.size:
        expected = part_path.stat().st_size if part_path.exists() else 0
        raise HTTPException(
            status_code=409,
            detail={"message": "文件尚未上传完成", "expected_offset": expected},
        )
    upload.status = "verifying"
    upload.updated = _utcnow()
    await db.flush()
    actual_hash = await asyncio.to_thread(_sha256_file, part_path)
    if actual_hash != upload.sha256:
        upload.status = "failed"
        upload.updated = _utcnow()
        await db.commit()
        raise HTTPException(status_code=400, detail="完整文件 SHA-256 校验失败")
    final_path = _upload_path(upload.id, ".bin")
    await asyncio.to_thread(os.replace, part_path, final_path)
    upload.stored_name = final_path.name
    upload.received_bytes = upload.size
    upload.status = "ready"
    upload.completed = upload.updated = _utcnow()
    _add_audit(
        db, request, user, "file_upload_complete", f"upload:{upload.id}",
        {"filename": upload.original_filename, "size": upload.size, "sha256": upload.sha256},
    )
    await db.flush()
    return _serialize_upload(upload)


@router.delete("/uploads/{upload_id}", status_code=204)
async def cancel_upload(
    upload_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    upload = await _get_upload_for_user(upload_id, user, db)
    active = await db.scalar(
        select(FileTransfer.id).where(
            FileTransfer.source == upload.id,
            FileTransfer.status.in_(["queued", "running"]),
        ).limit(1)
    )
    if active:
        raise HTTPException(status_code=409, detail="文件正在分发，不能删除")
    for suffix in (".part", ".bin"):
        await asyncio.to_thread(_upload_path(upload.id, suffix).unlink, True)
    upload.status = "cancelled"
    upload.updated = _utcnow()
    _add_audit(
        db, request, user, "file_upload_cancel", f"upload:{upload.id}",
        {"filename": upload.original_filename},
    )
    return Response(status_code=204)


@router.post("/transfers", status_code=202)
async def create_transfer(
    body: TransferCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    unique_targets = list(dict.fromkeys(body.target_node_ids))
    if len(unique_targets) > settings.MAX_TRANSFER_TARGETS:
        raise HTTPException(status_code=400, detail="目标节点数量超过限制")
    upload = await _get_upload_for_user(body.upload_id, user, db)
    if upload.status != "ready" or not _upload_path(upload.id, ".bin").is_file():
        raise HTTPException(status_code=409, detail="源文件尚未准备完成或已经过期")
    result = await db.execute(select(Node.id).where(Node.id.in_(unique_targets)))
    found = set(result.scalars().all())
    missing = [node_id for node_id in unique_targets if node_id not in found]
    if missing:
        raise HTTPException(status_code=400, detail=f"目标节点不存在：{', '.join(missing)}")

    transfer = FileTransfer(
        filename=upload.original_filename,
        size=upload.size,
        source=upload.id,
        targets=json.dumps(unique_targets),
        status="queued",
        sha256=upload.sha256,
        dest_path=body.dest_path,
        overwrite=body.overwrite,
        created_by=user.id,
    )
    db.add(transfer)
    await db.flush()
    target_rows = [
        FileTransferTarget(transfer_id=transfer.id, node_id=node_id, status="queued")
        for node_id in unique_targets
    ]
    db.add_all(target_rows)
    _add_audit(
        db, request, user, "file_transfer_start", f"transfer:{transfer.id}",
        {
            "filename": transfer.filename,
            "targets": unique_targets,
            "dest_path": transfer.dest_path,
            "overwrite": transfer.overwrite,
        },
    )
    await db.commit()
    file_transfer_service.start(transfer.id)
    return _serialize_transfer(transfer, target_rows)


@router.get("/transfers")
async def list_transfers(
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = select(FileTransfer).order_by(FileTransfer.created.desc()).limit(limit)
    if user.role != "admin":
        query = query.where(FileTransfer.created_by == user.id)
    result = await db.execute(query)
    transfers = result.scalars().all()
    if not transfers:
        return []
    target_result = await db.execute(
        select(FileTransferTarget).where(
            FileTransferTarget.transfer_id.in_([item.id for item in transfers])
        )
    )
    grouped: dict[str, list[FileTransferTarget]] = {}
    for target in target_result.scalars().all():
        grouped.setdefault(target.transfer_id, []).append(target)
    return [
        _serialize_transfer(transfer, grouped.get(transfer.id, []))
        for transfer in transfers
    ]


@router.get("/transfers/{transfer_id}")
async def get_transfer(
    transfer_id: str,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(get_current_user),
):
    transfer = await db.get(FileTransfer, transfer_id)
    if transfer is None or (user.role != "admin" and transfer.created_by != user.id):
        raise HTTPException(status_code=404, detail="传输任务不存在")
    result = await db.execute(
        select(FileTransferTarget).where(
            FileTransferTarget.transfer_id == transfer.id
        )
    )
    return _serialize_transfer(transfer, result.scalars().all())


@router.post("/transfers/{transfer_id}/retry", status_code=202)
async def retry_transfer(
    transfer_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_role("admin", "operator")),
):
    transfer = await db.get(FileTransfer, transfer_id)
    if transfer is None or (user.role != "admin" and transfer.created_by != user.id):
        raise HTTPException(status_code=404, detail="传输任务不存在")
    if file_transfer_service.is_running(transfer.id):
        raise HTTPException(status_code=409, detail="传输仍在运行，请等待本轮结束后再续传")
    upload = await db.get(FileUpload, transfer.source) if transfer.source else None
    if upload is None or upload.status != "ready" or not _upload_path(upload.id, ".bin").is_file():
        raise HTTPException(status_code=409, detail="源文件已经过期，无法续传")
    result = await db.execute(
        select(FileTransferTarget).where(
            FileTransferTarget.transfer_id == transfer.id,
            FileTransferTarget.status.in_(["paused", "failed"]),
        )
    )
    targets = result.scalars().all()
    if not targets:
        raise HTTPException(status_code=409, detail="没有可重试的节点")
    for target in targets:
        target.status = "queued"
        target.error = None
        target.finished = None
    transfer.status = "queued"
    transfer.finished = None
    _add_audit(
        db, request, user, "file_transfer_retry", f"transfer:{transfer.id}",
        {"target_ids": [target.node_id for target in targets]},
    )
    await db.commit()
    file_transfer_service.start(transfer.id, [target.id for target in targets])
    return _serialize_transfer(transfer, targets)
