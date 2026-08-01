from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import stat
import threading
import time
from pathlib import Path

try:
    from .update_package import (
        MAX_ENTRIES,
        MAX_EXPANDED_BYTES,
        MAX_PACKAGE_BYTES,
        RELEASE_ID_RE,
        SHA256_RE,
        UPDATER_VERSION,
        VERSION_RE,
        RuntimeIdentity,
        UpdatePackageError,
        current_runtime_identity,
        sha256_file,
        verify_update_package,
    )
except ImportError:  # Script execution from the Agent release directory.
    from update_package import (
        MAX_ENTRIES,
        MAX_EXPANDED_BYTES,
        MAX_PACKAGE_BYTES,
        RELEASE_ID_RE,
        SHA256_RE,
        UPDATER_VERSION,
        VERSION_RE,
        RuntimeIdentity,
        UpdatePackageError,
        current_runtime_identity,
        sha256_file,
        verify_update_package,
    )


UPDATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
PACKAGE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
DEFAULT_CHUNK_BYTES = 512 * 1024


def _atomic_json(path: Path, value: dict, *, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    try:
        temporary.chmod(mode)
    except OSError:
        pass
    os.replace(temporary, path)


def _read_json(path: Path, *, max_bytes: int = 64 * 1024) -> dict:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > max_bytes:
            raise ValueError("update state file is not a safe regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("update state is unavailable") from exc
    if not isinstance(value, dict):
        raise ValueError("update state is invalid")
    return value


def _safe_protocol_error(exc: Exception) -> str:
    if isinstance(exc, OSError):
        return "local update storage operation failed"
    message = " ".join(str(exc).split()) or "update operation failed"
    return message[:500]


class UpdateReceiver:
    """Receives signed update packages into a fixed local helper spool.

    This class deliberately cannot write to the Agent installation or releases.
    The privileged helper revalidates every committed package before activation.
    """

    def __init__(
        self,
        spool_root: Path,
        trusted_keys_dir: Path,
        *,
        enabled: bool = False,
        active_pointer: Path | None = None,
        max_package_bytes: int = MAX_PACKAGE_BYTES,
        max_expanded_bytes: int = MAX_EXPANDED_BYTES,
        max_entries: int = MAX_ENTRIES,
        chunk_bytes: int = DEFAULT_CHUNK_BYTES,
        min_free_bytes: int = 256 * 1024 * 1024,
        runtime: RuntimeIdentity | None = None,
        updater_version: str = UPDATER_VERSION,
    ) -> None:
        self.spool_root = Path(spool_root)
        self.trusted_keys_dir = Path(trusted_keys_dir)
        self.active_pointer = (
            Path(active_pointer)
            if active_pointer is not None
            else self.spool_root.parent / "active.json"
        )
        self.enabled = bool(enabled)
        self.max_package_bytes = max(1, int(max_package_bytes))
        self.max_expanded_bytes = max(1, int(max_expanded_bytes))
        self.max_entries = max(1, int(max_entries))
        self.chunk_bytes = max(64 * 1024, min(int(chunk_bytes), DEFAULT_CHUNK_BYTES))
        self.min_free_bytes = max(0, int(min_free_bytes))
        self.runtime = runtime or current_runtime_identity()
        self.updater_version = updater_version
        self._lock = threading.RLock()
        self._reported_status_mtimes: dict[str, tuple[str, int]] = {}

    @property
    def available(self) -> bool:
        if not self.enabled or self.runtime.os_family not in {"windows", "linux"}:
            return False
        try:
            return any(
                path.is_file() and not path.is_symlink()
                for path in self.trusted_keys_dir.glob("*.pub")
            )
        except OSError:
            return False

    def capabilities(self) -> dict:
        active = self.active_release()
        return {
            "update_protocol": 2 if self.available else 0,
            "updater_version": self.updater_version,
            "os_family": self.runtime.os_family,
            "architecture": self.runtime.arch,
            "python_abi": self.runtime.python_abi,
            "active_release_id": str(active.get("release_id") or ""),
            "active_version": str(active.get("version") or ""),
            "activation_execution_id": str(active.get("activation_execution_id") or ""),
            "activation_operation": str(active.get("activation_operation") or ""),
        }

    def active_release(self) -> dict:
        pointer = self.active_pointer
        if not pointer.is_file():
            return {}
        try:
            value = _read_json(pointer)
        except ValueError:
            return {}
        return {
            "release_id": value.get("release_id"),
            "version": value.get("version"),
            "activation_execution_id": value.get("activation_execution_id"),
            "activation_operation": value.get("activation_operation"),
        }

    def init(self, payload: dict) -> dict:
        with self._lock:
            try:
                self._require_available()
                self._ensure_layout()
                execution_id, transfer_id = self._ids(payload)
                size = self._positive_int(payload.get("size"), "package size")
                if size > self.max_package_bytes:
                    raise ValueError("update package exceeds the Agent size limit")
                sha256 = str(payload.get("sha256") or "").lower()
                if not SHA256_RE.fullmatch(sha256):
                    raise ValueError("invalid update package SHA-256")
                requested_chunk = self._positive_int(
                    payload.get("chunk_size", self.chunk_bytes),
                    "chunk size",
                )
                if requested_chunk > self.chunk_bytes:
                    raise ValueError("update chunk exceeds the Agent chunk limit")
                package_id = str(payload.get("package_id") or "")
                if package_id and not PACKAGE_ID_RE.fullmatch(package_id):
                    raise ValueError("invalid update package ID")
                expected_release = str(payload.get("release_id") or "")
                if expected_release and not RELEASE_ID_RE.fullmatch(expected_release):
                    raise ValueError("invalid expected release ID")
                expected_version = str(payload.get("version") or "")
                if expected_version and not VERSION_RE.fullmatch(expected_version):
                    raise ValueError("invalid expected Agent version")

                committed, committed_meta = self._rebind_committed_transfer(
                    execution_id,
                    transfer_id,
                    size=size,
                    sha256=sha256,
                    package_id=package_id,
                    release_id=expected_release,
                    version=expected_version,
                )
                if committed.is_file() and committed_meta.is_file():
                    metadata = _read_json(committed_meta)
                    if (
                        metadata.get("size") == size
                        and metadata.get("sha256") == sha256
                        and sha256_file(committed) == sha256
                    ):
                        return {
                            "success": True,
                            "phase": "init",
                            "execution_id": execution_id,
                            "transfer_id": transfer_id,
                            "next_offset": size,
                            "size": size,
                            "completed": True,
                            "release_id": metadata.get("release_id"),
                        }
                    raise ValueError("execution ID is already bound to another package")

                part_path, meta_path = self._incoming_paths(transfer_id)
                expected = {
                    "schema_version": 1,
                    "execution_id": execution_id,
                    "transfer_id": transfer_id,
                    "package_id": package_id,
                    "release_id": expected_release,
                    "version": expected_version,
                    "size": size,
                    "sha256": sha256,
                    "chunk_size": requested_chunk,
                    "updated_at": int(time.time()),
                }
                existing: dict | None = None
                if part_path.is_file() and meta_path.is_file():
                    try:
                        existing = _read_json(meta_path)
                    except ValueError:
                        existing = None
                comparable = dict(expected)
                comparable.pop("updated_at")
                comparable.pop("execution_id")
                existing_comparable = dict(existing or {})
                existing_comparable.pop("updated_at", None)
                existing_comparable.pop("execution_id", None)
                if existing_comparable != comparable:
                    part_path.unlink(missing_ok=True)
                    meta_path.unlink(missing_ok=True)
                    self._ensure_disk_capacity(size)
                    self._create_regular_file(part_path)
                    _atomic_json(meta_path, expected)
                elif existing is not None and existing.get("execution_id") != execution_id:
                    existing["execution_id"] = execution_id
                    existing["updated_at"] = int(time.time())
                    _atomic_json(meta_path, existing)
                received = part_path.stat().st_size
                if received > size:
                    part_path.unlink(missing_ok=True)
                    self._create_regular_file(part_path)
                    received = 0
                return {
                    "success": True,
                    "phase": "init",
                    "execution_id": execution_id,
                    "transfer_id": transfer_id,
                    "next_offset": received,
                    "size": size,
                }
            except Exception as exc:
                return {"success": False, "phase": "init", "error": _safe_protocol_error(exc)}

    def chunk(self, payload: dict) -> dict:
        with self._lock:
            try:
                self._require_available()
                execution_id, transfer_id = self._ids(payload)
                part_path, meta_path = self._incoming_paths(transfer_id)
                metadata = _read_json(meta_path)
                self._assert_session(metadata, execution_id, transfer_id)
                if not self._is_regular(part_path):
                    raise ValueError("update transfer is not initialized")
                offset = int(payload.get("offset", -1))
                chunk_sha256 = str(payload.get("chunk_sha256") or "").lower()
                if not SHA256_RE.fullmatch(chunk_sha256):
                    raise ValueError("invalid update chunk SHA-256")
                raw = base64.b64decode(
                    str(payload.get("content_b64") or ""),
                    validate=True,
                )
                if not raw or len(raw) > int(metadata["chunk_size"]):
                    raise ValueError("invalid update chunk size")
                if hashlib.sha256(raw).hexdigest() != chunk_sha256:
                    raise ValueError("update chunk SHA-256 mismatch")

                received = part_path.stat().st_size
                if offset == received:
                    if received + len(raw) > int(metadata["size"]):
                        raise ValueError("update chunk exceeds the declared package size")
                    with part_path.open("ab") as destination:
                        destination.write(raw)
                        destination.flush()
                        os.fsync(destination.fileno())
                    received += len(raw)
                elif 0 <= offset < received and offset + len(raw) <= received:
                    with part_path.open("rb") as existing:
                        existing.seek(offset)
                        if existing.read(len(raw)) != raw:
                            raise ValueError(f"update chunk offset mismatch; expected {received}")
                else:
                    raise ValueError(f"update chunk offset mismatch; expected {received}")
                metadata["updated_at"] = int(time.time())
                _atomic_json(meta_path, metadata)
                return {
                    "success": True,
                    "phase": "chunk",
                    "execution_id": execution_id,
                    "transfer_id": transfer_id,
                    "next_offset": received,
                    "size": int(metadata["size"]),
                }
            except Exception as exc:
                return {"success": False, "phase": "chunk", "error": _safe_protocol_error(exc)}

    def commit(self, payload: dict) -> dict:
        with self._lock:
            try:
                self._require_available()
                execution_id, transfer_id = self._ids(payload)
                part_path, meta_path = self._incoming_paths(transfer_id)
                metadata = _read_json(meta_path)
                self._assert_session(metadata, execution_id, transfer_id)
                if not self._is_regular(part_path):
                    raise ValueError("update transfer is not initialized")
                requested_sha = str(payload.get("sha256") or "").lower()
                if requested_sha != metadata.get("sha256"):
                    raise ValueError("commit SHA-256 differs from the initialized transfer")
                if part_path.stat().st_size != int(metadata["size"]):
                    raise ValueError("update package is incomplete")
                if sha256_file(part_path) != metadata["sha256"]:
                    raise ValueError("complete update package SHA-256 mismatch")

                verified = verify_update_package(
                    part_path,
                    self.trusted_keys_dir,
                    max_package_bytes=self.max_package_bytes,
                    max_expanded_bytes=self.max_expanded_bytes,
                    max_entries=self.max_entries,
                    runtime=self.runtime,
                    updater_version=self.updater_version,
                )
                if (
                    metadata.get("release_id")
                    and metadata["release_id"] != verified.release_id
                ):
                    raise ValueError("signed release ID differs from the transfer request")
                if metadata.get("version") and metadata["version"] != verified.version:
                    raise ValueError("signed version differs from the transfer request")
                committed, committed_meta = self._committed_paths(execution_id)
                if committed.exists() or committed_meta.exists():
                    raise ValueError("execution ID already has a committed package")
                os.replace(part_path, committed)
                committed_state = {
                    "schema_version": 1,
                    "execution_id": execution_id,
                    "transfer_id": transfer_id,
                    "package_id": metadata.get("package_id", ""),
                    "release_id": verified.release_id,
                    "version": verified.version,
                    "size": verified.package_size,
                    "sha256": verified.package_sha256,
                    "key_id": verified.key_id,
                    "target_os": verified.target_os,
                    "target_arch": verified.target_arch,
                    "python_abi": verified.python_abi,
                    "entrypoint": verified.entrypoint,
                    "committed_at": int(time.time()),
                }
                _atomic_json(committed_meta, committed_state)
                meta_path.unlink(missing_ok=True)
                self._write_status(execution_id, {
                    "stage": "staged",
                    "progress": 100,
                    "release_id": verified.release_id,
                    "version": verified.version,
                })
                return {
                    "success": True,
                    "phase": "commit",
                    **committed_state,
                }
            except Exception as exc:
                return {"success": False, "phase": "commit", "error": _safe_protocol_error(exc)}

    def activate(self, payload: dict) -> dict:
        with self._lock:
            try:
                self._require_available()
                execution_id = self._execution_id(payload)
                package, meta_path = self._committed_paths(execution_id)
                if not self._is_regular(package):
                    raise ValueError("signed update package has not been committed")
                metadata = _read_json(meta_path)
                release_id = str(payload.get("release_id") or metadata.get("release_id") or "")
                if release_id != metadata.get("release_id"):
                    raise ValueError("activation release ID does not match the committed package")
                version = str(payload.get("version") or metadata.get("version") or "")
                if version != metadata.get("version"):
                    raise ValueError("activation version does not match the committed package")
                package_sha256 = str(
                    payload.get("package_sha256") or metadata.get("sha256") or ""
                ).lower()
                if package_sha256 != metadata.get("sha256"):
                    raise ValueError("activation package SHA-256 does not match staged content")
                health_timeout = int(payload.get("health_timeout", 120))
                if health_timeout != 120:
                    raise ValueError("Agent update health timeout must be 120 seconds")
                deployment_id = str(payload.get("deployment_id") or "")
                if deployment_id and not PACKAGE_ID_RE.fullmatch(deployment_id):
                    raise ValueError("invalid update deployment ID")
                request = {
                    "schema_version": 1,
                    "action": "activate",
                    "execution_id": execution_id,
                    "release_id": release_id,
                    "package_sha256": metadata["sha256"],
                    "package_size": metadata["size"],
                    "deployment_id": deployment_id,
                    "health_timeout": health_timeout,
                    "created_at": int(time.time()),
                }
                self._queue_request(execution_id, request)
                self._write_status(execution_id, {
                    "stage": "activation_queued",
                    "progress": 100,
                    "release_id": release_id,
                    "version": metadata.get("version"),
                })
                return {
                    "success": True,
                    "phase": "activate",
                    "execution_id": execution_id,
                    "release_id": release_id,
                    "stage": "activation_queued",
                }
            except Exception as exc:
                return {"success": False, "phase": "activate", "error": _safe_protocol_error(exc)}

    def rollback(self, payload: dict) -> dict:
        with self._lock:
            try:
                self._require_available()
                execution_id = self._execution_id(payload)
                source_execution_id = str(payload.get("source_execution_id") or "")
                if source_execution_id and not UPDATE_ID_RE.fullmatch(source_execution_id):
                    raise ValueError("invalid source update execution ID")
                deployment_id = str(payload.get("deployment_id") or "")
                if deployment_id and not PACKAGE_ID_RE.fullmatch(deployment_id):
                    raise ValueError("invalid update deployment ID")
                target_release_id = str(payload.get("target_release_id") or "")
                target_version = str(payload.get("target_version") or "")
                if not RELEASE_ID_RE.fullmatch(target_release_id):
                    raise ValueError("invalid rollback release ID")
                if not VERSION_RE.fullmatch(target_version):
                    raise ValueError("invalid rollback version")
                health_timeout = int(payload.get("health_timeout", 120))
                if health_timeout != 120:
                    raise ValueError("Agent rollback health timeout must be 120 seconds")
                request = {
                    "schema_version": 1,
                    "action": "rollback",
                    "execution_id": execution_id,
                    "source_execution_id": source_execution_id,
                    "deployment_id": deployment_id,
                    "target_release_id": target_release_id,
                    "target_version": target_version,
                    "health_timeout": health_timeout,
                    "created_at": int(time.time()),
                }
                self._queue_request(execution_id, request)
                self._write_status(execution_id, {
                    "stage": "rollback_queued",
                    "progress": 0,
                    "source_execution_id": source_execution_id,
                })
                return {
                    "success": True,
                    "phase": "rollback",
                    "execution_id": execution_id,
                    "stage": "rollback_queued",
                }
            except Exception as exc:
                return {"success": False, "phase": "rollback", "error": _safe_protocol_error(exc)}

    def status(self, payload: dict) -> dict:
        with self._lock:
            try:
                execution_id = self._execution_id(payload)
                status_path = self._helper_status_path(execution_id)
                if not status_path.is_file():
                    status_path = self._agent_status_path(execution_id)
                if not status_path.is_file():
                    raise ValueError("update execution status is unavailable")
                return {
                    "success": True,
                    "phase": "status",
                    **_read_json(status_path),
                }
            except Exception as exc:
                return {"success": False, "phase": "status", "error": _safe_protocol_error(exc)}

    def cancel(self, payload: dict) -> dict:
        with self._lock:
            try:
                execution_id, transfer_id = self._ids(payload)
                part, meta = self._incoming_paths(transfer_id)
                part.unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
                request = self._request_path(execution_id)
                processing = self._processing_path(execution_id)
                active = self.active_release()
                if active.get("activation_execution_id") == execution_id:
                    _atomic_json(self._cancel_path(execution_id), {
                        "schema_version": 1,
                        "execution_id": execution_id,
                        "created_at": int(time.time()),
                    })
                    stage = "cancel_requested"
                elif request.is_file() and not processing.exists():
                    request.unlink(missing_ok=True)
                    stage = "cancelled"
                elif processing.exists():
                    _atomic_json(self._cancel_path(execution_id), {
                        "schema_version": 1,
                        "execution_id": execution_id,
                        "created_at": int(time.time()),
                    })
                    stage = "cancel_requested"
                else:
                    package, package_meta = self._committed_paths(execution_id)
                    package.unlink(missing_ok=True)
                    package_meta.unlink(missing_ok=True)
                    stage = "cancelled"
                self._write_status(execution_id, {"stage": stage, "progress": 0})
                return {
                    "success": True,
                    "phase": "cancel",
                    "execution_id": execution_id,
                    "transfer_id": transfer_id,
                    "stage": stage,
                }
            except Exception as exc:
                return {"success": False, "phase": "cancel", "error": _safe_protocol_error(exc)}

    def health_ack(self, payload: dict) -> dict:
        with self._lock:
            try:
                execution_id = self._execution_id(payload)
                release_id = str(payload.get("release_id") or "")
                if payload.get("success") is not True:
                    raise ValueError("Server did not accept the Agent update health report")
                if not RELEASE_ID_RE.fullmatch(release_id):
                    raise ValueError("invalid healthy release ID")
                _atomic_json(self._health_path(execution_id), {
                    "schema_version": 1,
                    "execution_id": execution_id,
                    "release_id": release_id,
                    "confirmed_at": int(time.time()),
                })
                return {
                    "success": True,
                    "phase": "health_ack",
                    "execution_id": execution_id,
                    "release_id": release_id,
                }
            except Exception as exc:
                return {"success": False, "phase": "health_ack", "error": _safe_protocol_error(exc)}

    def changed_statuses(self, *, include_all: bool = False) -> list[dict]:
        with self._lock:
            statuses: list[dict] = []
            selected: dict[str, tuple[str, Path]] = {}
            # Agent-written transfer progress is kept separate from privileged
            # helper results.  Once a helper result exists it is authoritative
            # for that execution and cannot be replaced by the Agent account.
            for source, directory in (
                ("agent", self.spool_root / "agent-status"),
                ("helper", self.spool_root / "helper-status"),
            ):
                if not directory.is_dir():
                    continue
                for path in sorted(directory.glob("*.json")):
                    execution_id = path.stem
                    if UPDATE_ID_RE.fullmatch(execution_id):
                        selected[execution_id] = (source, path)
            for execution_id, (source, path) in sorted(selected.items()):
                try:
                    mtime = path.stat().st_mtime_ns
                    if (
                        not include_all
                        and self._reported_status_mtimes.get(execution_id)
                        == (source, mtime)
                    ):
                        continue
                    value = _read_json(path)
                except (OSError, ValueError):
                    continue
                self._reported_status_mtimes[execution_id] = (source, mtime)
                statuses.append(value)
            return statuses

    def _require_available(self) -> None:
        if not self.available:
            raise ValueError("trusted Agent updates are disabled or no trusted key is installed")

    def _ensure_layout(self) -> None:
        root = self.spool_root
        root.mkdir(parents=True, exist_ok=True)
        if root.is_symlink():
            raise ValueError("update spool root cannot be a symbolic link")
        for name in (
            "incoming",
            "packages",
            "requests",
            "agent-status",
            "health",
            "cancel",
        ):
            path = root / name
            path.mkdir(mode=0o700, exist_ok=True)
            if path.is_symlink():
                raise ValueError("update spool directories cannot be symbolic links")

    def _ensure_disk_capacity(self, package_size: int) -> None:
        free = shutil.disk_usage(self.spool_root).free
        if free - package_size < self.min_free_bytes:
            raise ValueError("insufficient free disk space for the update package")

    def _incoming_paths(self, transfer_id: str) -> tuple[Path, Path]:
        return (
            self.spool_root / "incoming" / f"{transfer_id}.part",
            self.spool_root / "incoming" / f"{transfer_id}.json",
        )

    def _committed_paths(self, execution_id: str) -> tuple[Path, Path]:
        return (
            self.spool_root / "packages" / f"{execution_id}.wcmupd",
            self.spool_root / "packages" / f"{execution_id}.json",
        )

    def _rebind_committed_transfer(
        self,
        execution_id: str,
        transfer_id: str,
        *,
        size: int,
        sha256: str,
        package_id: str,
        release_id: str,
        version: str,
    ) -> tuple[Path, Path]:
        destination, destination_meta = self._committed_paths(execution_id)
        if destination.exists() or destination_meta.exists():
            return destination, destination_meta
        packages = self.spool_root / "packages"
        if not packages.is_dir():
            return destination, destination_meta
        matches: list[tuple[Path, Path, dict]] = []
        for candidate_meta in packages.glob("*.json"):
            if not UPDATE_ID_RE.fullmatch(candidate_meta.stem):
                continue
            candidate_package = packages / f"{candidate_meta.stem}.wcmupd"
            try:
                metadata = _read_json(candidate_meta)
            except ValueError:
                continue
            if (
                metadata.get("transfer_id") == transfer_id
                and metadata.get("size") == size
                and metadata.get("sha256") == sha256
                and metadata.get("package_id", "") == package_id
                and metadata.get("release_id", "") == release_id
                and metadata.get("version", "") == version
                and self._is_regular(candidate_package)
                and sha256_file(candidate_package) == sha256
            ):
                matches.append((candidate_package, candidate_meta, metadata))
        if len(matches) > 1:
            raise ValueError("update transfer has ambiguous committed state")
        if not matches:
            return destination, destination_meta
        old_package, old_meta, metadata = matches[0]
        metadata["execution_id"] = execution_id
        temporary_meta = old_meta.with_suffix(".rebind.tmp")
        _atomic_json(temporary_meta, metadata)
        os.replace(old_package, destination)
        os.replace(temporary_meta, destination_meta)
        old_meta.unlink(missing_ok=True)
        return destination, destination_meta

    def _request_path(self, execution_id: str) -> Path:
        return self.spool_root / "requests" / f"{execution_id}.json"

    def _processing_path(self, execution_id: str) -> Path:
        return self.spool_root / "processing" / f"{execution_id}.json"

    def _agent_status_path(self, execution_id: str) -> Path:
        return self.spool_root / "agent-status" / f"{execution_id}.json"

    def _helper_status_path(self, execution_id: str) -> Path:
        return self.spool_root / "helper-status" / f"{execution_id}.json"

    def _health_path(self, execution_id: str) -> Path:
        return self.spool_root / "health" / f"{execution_id}.json"

    def _cancel_path(self, execution_id: str) -> Path:
        return self.spool_root / "cancel" / f"{execution_id}.json"

    def _queue_request(self, execution_id: str, request: dict) -> None:
        self._ensure_layout()
        request_path = self._request_path(execution_id)
        processing_path = self._processing_path(execution_id)
        if request_path.exists() or processing_path.exists():
            raise ValueError("update execution is already queued or running")
        _atomic_json(request_path, request)

    def _write_status(self, execution_id: str, fields: dict) -> None:
        value = {
            "schema_version": 1,
            "execution_id": execution_id,
            "updated_at": int(time.time()),
            **fields,
        }
        _atomic_json(self._agent_status_path(execution_id), value, mode=0o640)

    @staticmethod
    def _assert_session(metadata: dict, execution_id: str, transfer_id: str) -> None:
        if (
            metadata.get("schema_version") != 1
            or metadata.get("execution_id") != execution_id
            or metadata.get("transfer_id") != transfer_id
        ):
            raise ValueError("update transfer identity does not match its session")

    @staticmethod
    def _create_regular_file(path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(descriptor)

    @staticmethod
    def _is_regular(path: Path) -> bool:
        try:
            info = path.lstat()
        except OSError:
            return False
        return stat.S_ISREG(info.st_mode) and not path.is_symlink()

    @staticmethod
    def _positive_int(value: object, label: str) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"invalid {label}") from exc
        if parsed <= 0:
            raise ValueError(f"invalid {label}")
        return parsed

    @staticmethod
    def _execution_id(payload: dict) -> str:
        execution_id = str(payload.get("execution_id") or "")
        if not UPDATE_ID_RE.fullmatch(execution_id):
            raise ValueError("invalid update execution ID")
        return execution_id

    @classmethod
    def _ids(cls, payload: dict) -> tuple[str, str]:
        execution_id = cls._execution_id(payload)
        transfer_id = str(payload.get("transfer_id") or execution_id)
        if not UPDATE_ID_RE.fullmatch(transfer_id):
            raise ValueError("invalid update transfer ID")
        return execution_id, transfer_id
