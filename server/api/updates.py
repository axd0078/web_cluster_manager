from __future__ import annotations

import json
import uuid
import hashlib
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core.connection_manager import manager
from database import get_db
from middleware.auth import get_current_user, require_role
from models.node import Node, UpdatePackage

router = APIRouter(prefix="/api/v2/updates", tags=["updates"])


async def _read_limited(file: UploadFile) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > settings.MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="更新包超过允许大小")
        chunks.append(chunk)
    return b"".join(chunks)


@router.post("/packages", status_code=201)
async def create_package(
    version: str = Query(...),
    description: str = Query(""),
    file: UploadFile | None = None,
    _user=Depends(require_role("admin")),
    db: AsyncSession = Depends(get_db),
):
    pkg_id = str(uuid.uuid4())
    pkg_dir = settings.UPDATES_DIR / pkg_id
    pkg_dir.mkdir(parents=True, exist_ok=True)

    content = b""
    safe_filename = None
    if file:
        content = await _read_limited(file)
        safe_filename = Path(file.filename or "update.zip").name
        if safe_filename != file.filename:
            raise HTTPException(status_code=400, detail="非法更新包文件名")
        target = (pkg_dir / safe_filename).resolve()
        if pkg_dir.resolve() not in target.parents:
            raise HTTPException(status_code=400, detail="非法更新包路径")
        target.write_bytes(content)

    pkg = UpdatePackage(
        id=pkg_id, version=version, description=description,
        filename=safe_filename,
        size=len(content) if file else 0,
        sha256=hashlib.sha256(content).hexdigest() if file else None,
    )
    db.add(pkg)
    await db.flush()

    return {
        "id": pkg.id, "version": pkg.version, "description": pkg.description,
        "filename": pkg.filename, "size": pkg.size,
        "created": pkg.created.isoformat() if pkg.created else None,
    }


@router.get("/packages")
async def list_packages(
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(
        select(UpdatePackage).order_by(UpdatePackage.created.desc())
    )
    packages = result.scalars().all()
    return [
        {
            "id": p.id, "version": p.version, "description": p.description,
            "filename": p.filename, "size": p.size, "sha256": p.sha256,
            "created": p.created.isoformat() if p.created else None,
        }
        for p in packages
    ]


@router.post("/push")
async def push_update(
    package_id: str = Query(...),
    target_node_ids: list[str] = Query(...),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role("admin")),
):
    result = await db.execute(select(UpdatePackage).where(UpdatePackage.id == package_id))
    pkg = result.scalar_one_or_none()
    if pkg is None:
        raise HTTPException(status_code=404, detail="更新包不存在")

    pkg_dir = settings.UPDATES_DIR / package_id
    file_path = pkg_dir / pkg.filename if pkg.filename else None

    import base64
    b64_content = ""
    if file_path and file_path.exists():
        b64_content = base64.b64encode(file_path.read_bytes()).decode()

    results: dict[str, str] = {}
    for node_id in target_node_ids:
        try:
            response = await manager.request_agent(node_id, {
                "type": "update", "request_id": f"{package_id}:{node_id}",
                "payload": {
                    "type": "full", "version": pkg.version,
                    "filename": pkg.filename, "zip_data": b64_content,
                    "sha256": pkg.sha256,
                },
            }, timeout=120)
            results[node_id] = "completed" if response.get("success") else "failed"
        except (ConnectionError, TimeoutError):
            results[node_id] = "agent_offline"

    return {"package_id": package_id, "results": results}


@router.get("/nodes/{node_id}/version")
async def get_node_version(
    node_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(get_current_user),
):
    result = await db.execute(select(Node).where(Node.id == node_id))
    node = result.scalar_one_or_none()
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    return {"node_id": node.id, "version": node.version, "status": node.status}
