from __future__ import annotations

import base64
import hashlib
import json
import shutil
import zipfile
from pathlib import Path


class AgentUpdater:
    MAX_UNCOMPRESSED_BYTES = 500 * 1024 * 1024

    def __init__(self, current_dir: Path, backup_dir: Path):
        self.current_dir = current_dir.resolve()
        self.backup_dir = backup_dir.resolve()

    def _safe_destination(self, relative: str) -> Path:
        candidate = (self.current_dir / relative).resolve()
        if candidate != self.current_dir and self.current_dir not in candidate.parents:
            raise ValueError(f"update path escapes agent directory: {relative}")
        return candidate

    def apply_update(self, update_data: dict) -> dict:
        try:
            self._create_backup()
            update_type = update_data.get("type", "full")
            if update_type == "full":
                raw = base64.b64decode(update_data.get("zip_data", ""), validate=True)
                expected = str(update_data.get("sha256") or "")
                actual = hashlib.sha256(raw).hexdigest()
                if not expected or actual != expected:
                    raise ValueError("update package SHA-256 mismatch")
                self._apply_full(raw)
            elif update_type == "incremental":
                self._apply_incremental(update_data)
            else:
                raise ValueError("unsupported update type")
            (self.current_dir / "version.json").write_text(
                json.dumps({"version": update_data.get("version", "unknown")}, indent=2),
                encoding="utf-8",
            )
            return {"success": True, "message": "更新已验证并应用，需要重启生效"}
        except Exception as exc:
            self._rollback()
            return {"success": False, "error": str(exc)}

    def _create_backup(self) -> None:
        if self.backup_dir.exists():
            shutil.rmtree(self.backup_dir)
        self.backup_dir.mkdir(parents=True)
        for source in self.current_dir.rglob("*"):
            if not source.is_file() or self.backup_dir in source.parents or "backup" in source.parts:
                continue
            relative = source.relative_to(self.current_dir)
            target = self.backup_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

    def _apply_full(self, raw: bytes) -> None:
        archive_path = self.current_dir / "_update.zip"
        archive_path.write_bytes(raw)
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                total = sum(info.file_size for info in archive.infolist())
                if total > self.MAX_UNCOMPRESSED_BYTES:
                    raise ValueError("update package expands beyond allowed size")
                for info in archive.infolist():
                    target = self._safe_destination(info.filename)
                    if info.is_dir():
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)
        finally:
            archive_path.unlink(missing_ok=True)

    def _apply_incremental(self, data: dict) -> None:
        files = data.get("files") if isinstance(data.get("files"), dict) else {}
        for relative, encoded in files.items():
            target = self._safe_destination(str(relative))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(encoded, validate=True))

    def _rollback(self) -> None:
        if not self.backup_dir.exists():
            return
        for source in self.backup_dir.rglob("*"):
            if source.is_file():
                relative = source.relative_to(self.backup_dir)
                target = self._safe_destination(str(relative))
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
