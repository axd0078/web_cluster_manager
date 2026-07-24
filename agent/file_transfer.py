from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

TRANSFER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_relative_path(value: str) -> str:
    raw = value.strip()
    if not raw or len(raw) > 500 or "\x00" in raw:
        raise ValueError("destination path is empty or too long")
    windows = PureWindowsPath(raw)
    posix = PurePosixPath(raw.replace("\\", "/"))
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("absolute destination paths are not allowed")
    parts = posix.parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise ValueError("destination contains an unsafe path component")
    for part in parts:
        if ":" in part:
            raise ValueError("drive names and NTFS alternate streams are not allowed")
        stem = part.rstrip(" .").split(".", 1)[0].upper()
        if stem in WINDOWS_RESERVED:
            raise ValueError("destination contains a reserved Windows device name")
    return "/".join(parts)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


class FileReceiver:
    """Persistent, sequential and resumable receiver rooted in one sandbox."""

    def __init__(
        self, root: Path, max_bytes: int = 100 * 1024 * 1024,
        chunk_bytes: int = 512 * 1024,
    ) -> None:
        self.root = root
        self.max_bytes = max(1, int(max_bytes))
        self.chunk_bytes = max(64 * 1024, min(int(chunk_bytes), 1024 * 1024))

    def init(self, payload: dict) -> dict:
        try:
            transfer_id = self._transfer_id(payload)
            relative = validate_relative_path(str(payload.get("dest_path") or ""))
            size = int(payload.get("size", -1))
            sha256 = str(payload.get("sha256") or "").lower()
            requested_chunk = int(payload.get("chunk_size", self.chunk_bytes))
            overwrite = bool(payload.get("overwrite", False))
            if size <= 0 or size > self.max_bytes:
                raise ValueError("file exceeds agent transfer limit")
            if not SHA256_RE.fullmatch(sha256):
                raise ValueError("invalid file SHA-256")
            if requested_chunk <= 0 or requested_chunk > self.chunk_bytes:
                raise ValueError("invalid file chunk size")

            self.root.mkdir(parents=True, exist_ok=True)
            partial_dir = self._partial_dir()
            partial_dir.mkdir(parents=True, exist_ok=True)
            destination = self._destination(relative)
            if destination.is_file():
                if (
                    destination.stat().st_size == size
                    and _sha256_file(destination) == sha256
                ):
                    return {
                        "success": True,
                        "phase": "init",
                        "next_offset": size,
                        "size": size,
                        "completed": True,
                    }
                if not overwrite:
                    raise ValueError("destination already exists and overwrite is disabled")
            elif destination.exists():
                raise ValueError("destination exists and is not a regular file")

            part_path, meta_path = self._state_paths(transfer_id)
            expected = {
                "transfer_id": transfer_id,
                "dest_path": relative,
                "size": size,
                "sha256": sha256,
                "overwrite": overwrite,
                "chunk_size": requested_chunk,
            }
            current: dict | None = None
            if meta_path.is_file() and part_path.is_file():
                try:
                    current = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    current = None
            if current != expected:
                part_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                part_path.touch(mode=0o600, exist_ok=False)
                self._write_metadata(meta_path, expected)
            received = part_path.stat().st_size
            if received > size:
                part_path.unlink(missing_ok=True)
                part_path.touch(mode=0o600, exist_ok=False)
                received = 0
            return {
                "success": True,
                "phase": "init",
                "next_offset": received,
                "size": size,
            }
        except Exception as exc:
            return {"success": False, "phase": "init", "error": str(exc)}

    def chunk(self, payload: dict) -> dict:
        try:
            transfer_id = self._transfer_id(payload)
            part_path, meta_path = self._state_paths(transfer_id)
            metadata = self._load_metadata(meta_path)
            if not part_path.is_file():
                raise ValueError("transfer session is not initialized")
            offset = int(payload.get("offset", -1))
            chunk_sha256 = str(payload.get("chunk_sha256") or "").lower()
            if not SHA256_RE.fullmatch(chunk_sha256):
                raise ValueError("invalid chunk SHA-256")
            raw = base64.b64decode(
                str(payload.get("content_b64") or ""), validate=True,
            )
            if not raw or len(raw) > int(metadata["chunk_size"]):
                raise ValueError("invalid file chunk size")
            if hashlib.sha256(raw).hexdigest() != chunk_sha256:
                raise ValueError("chunk SHA-256 mismatch")

            received = part_path.stat().st_size
            if offset == received:
                if received + len(raw) > int(metadata["size"]):
                    raise ValueError("chunk exceeds declared file size")
                with part_path.open("ab") as destination:
                    destination.write(raw)
                    destination.flush()
                    os.fsync(destination.fileno())
                received += len(raw)
            elif 0 <= offset < received and offset + len(raw) <= received:
                # A reply may be lost after the write. Accept an identical replay.
                with part_path.open("rb") as existing:
                    existing.seek(offset)
                    if existing.read(len(raw)) != raw:
                        raise ValueError(f"chunk offset mismatch; expected {received}")
            else:
                raise ValueError(f"chunk offset mismatch; expected {received}")
            return {
                "success": True,
                "phase": "chunk",
                "next_offset": received,
                "size": int(metadata["size"]),
            }
        except Exception as exc:
            return {"success": False, "phase": "chunk", "error": str(exc)}

    def commit(self, payload: dict) -> dict:
        try:
            transfer_id = self._transfer_id(payload)
            part_path, meta_path = self._state_paths(transfer_id)
            metadata = self._load_metadata(meta_path)
            if not part_path.is_file():
                raise ValueError("transfer session is not initialized")
            requested_sha = str(payload.get("sha256") or "").lower()
            if requested_sha != metadata["sha256"]:
                raise ValueError("commit SHA-256 does not match initialized transfer")
            if part_path.stat().st_size != int(metadata["size"]):
                raise ValueError("file is incomplete")
            actual_sha = _sha256_file(part_path)
            if actual_sha != metadata["sha256"]:
                raise ValueError("complete file SHA-256 mismatch")

            destination = self._destination(str(metadata["dest_path"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Resolve again after directory creation to catch an existing symlink.
            destination = self._destination(str(metadata["dest_path"]))
            if destination.exists() and not bool(metadata["overwrite"]):
                raise ValueError("destination already exists and overwrite is disabled")
            os.replace(part_path, destination)
            meta_path.unlink(missing_ok=True)
            return {
                "success": True,
                "phase": "commit",
                "path": str(metadata["dest_path"]),
                "size": int(metadata["size"]),
                "sha256": actual_sha,
            }
        except Exception as exc:
            return {"success": False, "phase": "commit", "error": str(exc)}

    def _partial_dir(self) -> Path:
        root = self.root.resolve()
        path = (root / ".wcm-partials").resolve()
        if root not in path.parents:
            raise ValueError("invalid partial transfer directory")
        return path

    def _state_paths(self, transfer_id: str) -> tuple[Path, Path]:
        partial_dir = self._partial_dir()
        return (
            partial_dir / f"{transfer_id}.part",
            partial_dir / f"{transfer_id}.json",
        )

    def _destination(self, relative: str) -> Path:
        normalized = validate_relative_path(relative)
        root = self.root.resolve()
        destination = (root / Path(*PurePosixPath(normalized).parts)).resolve()
        if destination == root or root not in destination.parents:
            raise ValueError("destination escapes transfer root")
        return destination

    @staticmethod
    def _transfer_id(payload: dict) -> str:
        transfer_id = str(payload.get("transfer_id") or "")
        if not TRANSFER_ID_RE.fullmatch(transfer_id):
            raise ValueError("invalid transfer id")
        return transfer_id

    @staticmethod
    def _load_metadata(meta_path: Path) -> dict:
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("transfer session is not initialized") from exc
        if not isinstance(metadata, dict):
            raise ValueError("invalid transfer metadata")
        return metadata

    @staticmethod
    def _write_metadata(path: Path, metadata: dict) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
            encoding="utf-8",
        )
        try:
            temporary.chmod(0o600)
        except OSError:
            pass
        os.replace(temporary, path)
