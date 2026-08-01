from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import venv
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

try:
    from .update_package import (
        MAX_ENTRIES,
        MAX_EXPANDED_BYTES,
        MAX_PACKAGE_BYTES,
        RELEASE_ID_RE,
        UPDATER_VERSION,
        VERSION_RE,
        VerifiedUpdatePackage,
        current_runtime_identity,
        sha256_file,
        validate_requirements_lock,
        verify_update_package,
    )
except ImportError:  # Installed as a standalone privileged runtime component.
    from update_package import (
        MAX_ENTRIES,
        MAX_EXPANDED_BYTES,
        MAX_PACKAGE_BYTES,
        RELEASE_ID_RE,
        UPDATER_VERSION,
        VERSION_RE,
        VerifiedUpdatePackage,
        current_runtime_identity,
        sha256_file,
        validate_requirements_lock,
        verify_update_package,
    )


UPDATE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")


HEALTH_TIMEOUT_SECONDS = 120
POLL_INTERVAL_SECONDS = 2
MAX_REQUEST_BYTES = 64 * 1024


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


def _safe_json(path: Path, *, max_bytes: int = MAX_REQUEST_BYTES) -> dict:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink() or info.st_size > max_bytes:
            raise ValueError("state is not a safe regular file")
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("state file is unavailable or invalid") from exc
    if not isinstance(value, dict):
        raise ValueError("state file must contain a JSON object")
    return value


def _is_regular(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink()


def _safe_rmtree(path: Path, parent: Path) -> None:
    try:
        resolved_parent = parent.resolve(strict=True)
        info = path.lstat()
    except OSError:
        return
    if path.is_symlink() or not stat.S_ISDIR(info.st_mode):
        raise ValueError("refusing to remove a non-directory release path")
    resolved = path.resolve(strict=True)
    if resolved_parent not in resolved.parents:
        raise ValueError("release cleanup path escapes its root")
    shutil.rmtree(path)


class UpdateHelper:
    """Privileged, filesystem-only Agent release activator.

    The helper has no listening socket and never accepts a command line or path
    from the Server. It consumes a fixed spool populated by the unprivileged
    Agent and independently verifies the signed package before touching the
    root/SYSTEM-owned release tree.
    """

    def __init__(
        self,
        install_root: Path,
        spool_root: Path,
        trusted_keys_dir: Path,
        *,
        health_timeout: int = HEALTH_TIMEOUT_SECONDS,
        min_free_bytes: int | None = None,
        now: Callable[[], float] = time.time,
        restart_agent: Callable[[], None] | None = None,
        install_release: Callable[[Path, dict], None] | None = None,
    ) -> None:
        self.install_root = Path(install_root)
        self.spool_root = Path(spool_root)
        self.trusted_keys_dir = Path(trusted_keys_dir)
        self.releases_root = self.install_root / "releases"
        self.control_root = self.install_root / "control"
        self.active_path = self.install_root / "active.json"
        self.pending_path = self.control_root / "pending_activation.json"
        self.history_path = self.control_root / "release_history.json"
        self.health_timeout = max(10, int(health_timeout))
        self.min_free_bytes = max(
            0,
            int(
                os.getenv("WCM_MIN_UPDATE_FREE_BYTES", str(256 * 1024 * 1024))
                if min_free_bytes is None
                else min_free_bytes
            ),
        )
        self.now = now
        self._restart_agent = restart_agent or self._restart_agent_service
        self._install_release = install_release or self._install_release_dependencies
        self.runtime = current_runtime_identity()

    def run_forever(self) -> None:
        while True:
            self.process_once()
            time.sleep(POLL_INTERVAL_SECONDS)

    def process_once(self) -> bool:
        self._ensure_layout()
        if self.pending_path.is_file():
            return self._process_pending()
        claimed = self._claim_request()
        if claimed is None:
            return False
        execution_id, processing_path = claimed
        try:
            request = self._load_request(processing_path, execution_id)
            if self._cancel_path(execution_id).is_file():
                self._write_status(execution_id, "cancelled", progress=0)
                return True
            if request["action"] == "activate":
                self._activate(request)
            else:
                self._rollback(request)
        except Exception as exc:
            self._write_status(
                execution_id,
                "failed",
                progress=0,
                error=self._sanitize_error(exc),
            )
        finally:
            processing_path.unlink(missing_ok=True)
        return True

    def _activate(self, request: dict) -> None:
        execution_id = request["execution_id"]
        self._write_status(execution_id, "verifying", progress=100)
        copied_package = self._copy_and_verify_source(request)
        try:
            verified = verify_update_package(
                copied_package,
                self.trusted_keys_dir,
                max_package_bytes=MAX_PACKAGE_BYTES,
                max_expanded_bytes=MAX_EXPANDED_BYTES,
                max_entries=MAX_ENTRIES,
                runtime=self.runtime,
                updater_version=UPDATER_VERSION,
            )
            if (
                verified.release_id != request["release_id"]
                or verified.package_sha256 != request["package_sha256"]
                or verified.package_size != request["package_size"]
            ):
                raise ValueError("signed package identity changed after staging")
            free_bytes = shutil.disk_usage(self.install_root).free
            if free_bytes - verified.expanded_size < self.min_free_bytes:
                raise ValueError("insufficient disk space for the expanded Agent release")
            if self._cancel_path(execution_id).is_file():
                self._write_status(execution_id, "cancelled", progress=0)
                return
            release = self._prepare_release(copied_package, verified, execution_id)
            if self._cancel_path(execution_id).is_file():
                self._write_status(execution_id, "cancelled", progress=0)
                return
            pointer = self._pointer_for_release(release, verified, execution_id)
            previous = self._read_active(required=False)
            self._begin_switch(
                execution_id,
                action="activate",
                previous=previous,
                target=pointer,
                health_timeout=request["health_timeout"],
            )
        finally:
            copied_package.unlink(missing_ok=True)

    def _rollback(self, request: dict) -> None:
        execution_id = request["execution_id"]
        current = self._read_active(required=True)
        target = self._select_rollback_target(
            current,
            request["target_release_id"],
            request["target_version"],
        )
        if target is None:
            raise ValueError("no verified previous Agent release is available")
        self._validate_release_pointer(target)
        target = {**target, "activation_execution_id": execution_id}
        target["activation_operation"] = "rollback"
        self._begin_switch(
            execution_id,
            action="rollback",
            previous=current,
            target=target,
            health_timeout=request["health_timeout"],
        )

    def _begin_switch(
        self,
        execution_id: str,
        *,
        action: str,
        previous: dict,
        target: dict,
        health_timeout: int,
    ) -> None:
        if previous:
            self._append_history(previous)
        pending = {
            "schema_version": 1,
            "execution_id": execution_id,
            "action": action,
            "previous": previous,
            "target": target,
            "deadline": int(self.now()) + min(self.health_timeout, health_timeout),
        }
        _atomic_json(self.pending_path, pending)
        _atomic_json(self.active_path, target, mode=0o644)
        self._write_status(
            execution_id,
            "activating",
            progress=100,
            action=action,
            release_id=target["release_id"],
            version=target["version"],
            previous_release_id=previous.get("release_id", ""),
        )
        try:
            self._restart_agent()
        except Exception:
            if previous:
                _atomic_json(self.active_path, previous, mode=0o644)
                try:
                    self._restart_agent()
                except Exception:
                    pass
            else:
                self.active_path.unlink(missing_ok=True)
            self.pending_path.unlink(missing_ok=True)
            raise ValueError("Agent service restart failed; the previous release was restored")
        self._write_status(
            execution_id,
            "awaiting_health",
            progress=100,
            action=action,
            release_id=target["release_id"],
            version=target["version"],
            previous_release_id=previous.get("release_id", ""),
            health_deadline=pending["deadline"],
        )

    def _process_pending(self) -> bool:
        try:
            pending = _safe_json(self.pending_path)
            self._validate_pending(pending)
            self._validate_release_pointer(pending["target"])
            if pending["previous"]:
                self._validate_release_pointer(pending["previous"])
        except Exception:
            # A corrupt root-owned pending marker is a local administrative
            # fault. Do not guess a target or process another activation.
            return False
        execution_id = pending["execution_id"]
        health_path = self._health_path(execution_id)
        if health_path.is_file():
            try:
                health = _safe_json(health_path)
            except ValueError:
                health = {}
            if (
                health.get("schema_version") == 1
                and health.get("execution_id") == execution_id
                and health.get("release_id") == pending["target"]["release_id"]
            ):
                active = self._read_active(required=True)
                if active.get("release_id") != pending["target"]["release_id"]:
                    self._rollback_pending(pending, "active release changed before health confirmation")
                    return True
                active["activation_execution_id"] = ""
                active["activation_operation"] = ""
                _atomic_json(self.active_path, active, mode=0o644)
                self.pending_path.unlink(missing_ok=True)
                health_path.unlink(missing_ok=True)
                self._cancel_path(execution_id).unlink(missing_ok=True)
                stage = "rolled_back" if pending["action"] == "rollback" else "completed"
                self._write_status(
                    execution_id,
                    stage,
                    progress=100,
                    action=pending["action"],
                    release_id=active["release_id"],
                    version=active["version"],
                )
                self._cleanup_staged_package(execution_id)
                self._prune_releases()
                return True
        if self.now() >= int(pending["deadline"]):
            self._rollback_pending(pending, "Agent health confirmation timed out")
            return True
        return False

    def _rollback_pending(self, pending: dict, reason: str) -> None:
        execution_id = pending["execution_id"]
        previous = pending["previous"]
        restart_error = ""
        if previous:
            _atomic_json(self.active_path, previous, mode=0o644)
            try:
                self._restart_agent()
            except Exception:
                restart_error = "; previous release restart also failed"
        else:
            self.active_path.unlink(missing_ok=True)
        self.pending_path.unlink(missing_ok=True)
        self._health_path(execution_id).unlink(missing_ok=True)
        self._cancel_path(execution_id).unlink(missing_ok=True)
        self._write_status(
            execution_id,
            "rolled_back" if previous else "failed",
            progress=100 if previous else 0,
            action=pending["action"],
            release_id=previous.get("release_id", ""),
            failed_release_id=pending["target"].get("release_id", ""),
            error=reason + restart_error,
        )
        self._cleanup_staged_package(execution_id)
        self._prune_releases()

    def _copy_and_verify_source(self, request: dict) -> Path:
        execution_id = request["execution_id"]
        source = self.spool_root / "packages" / f"{execution_id}.wcmupd"
        if not _is_regular(source):
            raise ValueError("staged package is not a regular file")
        incoming = self.control_root / "staging"
        incoming.mkdir(mode=0o700, parents=True, exist_ok=True)
        destination = incoming / f"{execution_id}.wcmupd"
        destination.unlink(missing_ok=True)
        source_fd = self._open_no_follow(source)
        try:
            source_info = os.fstat(source_fd)
            if (
                not stat.S_ISREG(source_info.st_mode)
                or source_info.st_size != request["package_size"]
                or source_info.st_size > MAX_PACKAGE_BYTES
            ):
                raise ValueError("staged package size changed")
            with os.fdopen(source_fd, "rb", closefd=False) as source_file:
                with destination.open("xb") as output:
                    shutil.copyfileobj(source_file, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
        finally:
            os.close(source_fd)
        if sha256_file(destination) != request["package_sha256"]:
            destination.unlink(missing_ok=True)
            raise ValueError("staged package SHA-256 changed")
        return destination

    def _prepare_release(
        self,
        package: Path,
        verified: VerifiedUpdatePackage,
        execution_id: str,
    ) -> Path:
        release = self._release_path(verified.release_id)
        if release.exists():
            self._validate_existing_release(release, verified)
            return release
        staging = self.releases_root / f".staging-{execution_id}"
        if staging.exists():
            _safe_rmtree(staging, self.releases_root)
        staging.mkdir(mode=0o700)
        try:
            payload_root = staging / "payload"
            payload_root.mkdir(mode=0o755)
            with zipfile.ZipFile(package) as archive:
                for item in verified.manifest["files"]:
                    relative = str(item["path"])
                    target = payload_root.joinpath(*PurePosixPath(relative).parts)
                    self._ensure_safe_parent(payload_root, target.parent)
                    info = archive.getinfo(f"payload/{relative}")
                    with archive.open(info) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output, length=1024 * 1024)
                        output.flush()
                        os.fsync(output.fileno())
            self._verify_extracted_payload(payload_root, verified)
            self._write_status(
                execution_id,
                "installing",
                progress=100,
                release_id=verified.release_id,
                version=verified.version,
            )
            self._install_release(staging, verified.manifest)
            _atomic_json(staging / "release.json", {
                "schema_version": 1,
                "release_id": verified.release_id,
                "version": verified.version,
                "entrypoint": verified.entrypoint,
                "package_sha256": verified.package_sha256,
                "key_id": verified.key_id,
            }, mode=0o644)
            self._seal_release_tree(staging)
            os.replace(staging, release)
        except Exception:
            if staging.exists():
                _safe_rmtree(staging, self.releases_root)
            raise
        return release

    def _seal_release_tree(self, release: Path) -> None:
        """Make a prepared release root-owned, immutable to the Agent and readable.

        The staging directory is intentionally created as 0700 while the helper
        builds it.  Before the atomic rename, the low-privilege Agent must be
        able to traverse and execute the release without being able to modify
        any of it.
        """
        if os.name == "nt":
            # Windows inherits the SYSTEM/Administrators full-control and Users
            # read/execute ACL installed on the release parent.
            return
        install_info = self.install_root.stat()
        for current, directories, files in os.walk(release, topdown=True, followlinks=False):
            current_path = Path(current)
            if current_path.is_symlink():
                raise ValueError("release directories cannot be symbolic links")
            for name in directories:
                path = current_path / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ValueError("release contains an unsafe directory")
                os.chown(path, install_info.st_uid, install_info.st_gid)
                path.chmod(0o755)
            for name in files:
                path = current_path / name
                info = path.lstat()
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ValueError("release contains an unsafe file")
                executable = bool(info.st_mode & 0o111)
                os.chown(path, install_info.st_uid, install_info.st_gid)
                path.chmod(0o755 if executable else 0o644)
            os.chown(current_path, install_info.st_uid, install_info.st_gid)
            current_path.chmod(0o755)

    def _install_release_dependencies(self, release: Path, manifest: dict) -> None:
        payload = release / "payload"
        lockfile = payload / "requirements.lock"
        if not _is_regular(lockfile):
            raise ValueError("release is missing requirements.lock")
        try:
            has_requirements = validate_requirements_lock(lockfile.read_bytes())
        except (OSError, ValueError) as exc:
            raise ValueError("release requirements.lock is unsafe") from exc
        wheelhouse = payload / "wheelhouse"
        if has_requirements and (not wheelhouse.is_dir() or wheelhouse.is_symlink()):
            raise ValueError("release with dependencies is missing its offline wheelhouse")
        if wheelhouse.exists():
            for path in wheelhouse.rglob("*"):
                if path.is_symlink() or (path.is_file() and path.suffix.lower() != ".whl"):
                    raise ValueError("offline wheelhouse may contain only regular wheel files")
                if not path.is_dir() and not _is_regular(path):
                    raise ValueError("offline wheelhouse contains an unsafe entry")
        virtualenv = release / "venv"
        venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(virtualenv)
        if not has_requirements:
            return
        python = self._venv_python(virtualenv)
        safe_environment = {
            key: value
            for key, value in os.environ.items()
            if key.upper() in {"SYSTEMROOT", "WINDIR", "COMSPEC", "TEMP", "TMP", "PATH"}
        }
        safe_environment["PYTHONNOUSERSITE"] = "1"
        result = subprocess.run(
            [
                str(python),
                "-I",
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--no-index",
                "--require-hashes",
                "--only-binary=:all:",
                "--no-deps",
                "--find-links",
                str(wheelhouse),
                "-r",
                str(lockfile),
            ],
            cwd=payload,
            env=safe_environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=300,
        )
        if result.returncode != 0:
            raise ValueError("offline, hash-locked dependency installation failed")

    def _validate_existing_release(
        self,
        release: Path,
        verified: VerifiedUpdatePackage,
    ) -> None:
        if release.is_symlink() or not release.is_dir():
            raise ValueError("release path is not a safe directory")
        metadata = _safe_json(release / "release.json")
        if (
            metadata.get("release_id") != verified.release_id
            or metadata.get("version") != verified.version
            or metadata.get("package_sha256") != verified.package_sha256
            or metadata.get("entrypoint") != verified.entrypoint
        ):
            raise ValueError("release ID is already occupied by different content")
        self._verify_extracted_payload(release / "payload", verified)
        if not self._venv_python(release / "venv").is_file():
            raise ValueError("existing release virtual environment is incomplete")

    @staticmethod
    def _verify_extracted_payload(
        payload_root: Path,
        verified: VerifiedUpdatePackage,
    ) -> None:
        for item in verified.manifest["files"]:
            path = payload_root.joinpath(*PurePosixPath(str(item["path"])).parts)
            if not _is_regular(path):
                raise ValueError("extracted release contains a non-regular file")
            if path.stat().st_size != int(item["size"]) or sha256_file(path) != item["sha256"]:
                raise ValueError("extracted release content differs from its signed manifest")

    def _pointer_for_release(
        self,
        release: Path,
        verified: VerifiedUpdatePackage,
        execution_id: str,
    ) -> dict:
        pointer = {
            "schema_version": 1,
            "release_id": verified.release_id,
            "version": verified.version,
            "entrypoint": verified.entrypoint,
            "activation_execution_id": execution_id,
            "activation_operation": "activate",
        }
        self._validate_release_pointer(pointer)
        return pointer

    def _read_active(self, *, required: bool) -> dict:
        if not self.active_path.is_file():
            if required:
                raise ValueError("active Agent release pointer is unavailable")
            return {}
        pointer = _safe_json(self.active_path)
        self._validate_release_pointer(pointer)
        return pointer

    def _validate_release_pointer(self, pointer: dict) -> None:
        required = {
            "schema_version",
            "release_id",
            "version",
            "entrypoint",
            "activation_execution_id",
            "activation_operation",
        }
        if set(pointer) != required or pointer.get("schema_version") != 1:
            raise ValueError("Agent release pointer has an invalid schema")
        release_id = pointer.get("release_id")
        if not isinstance(release_id, str) or not RELEASE_ID_RE.fullmatch(release_id):
            raise ValueError("Agent release pointer contains an invalid release ID")
        version = pointer.get("version")
        execution_id = pointer.get("activation_execution_id")
        operation = pointer.get("activation_operation")
        if (
            not isinstance(version, str)
            or not VERSION_RE.fullmatch(version)
            or not isinstance(execution_id, str)
            or (execution_id and not UPDATE_ID_RE.fullmatch(execution_id))
            or operation not in {"", "activate", "rollback"}
        ):
            raise ValueError("Agent release pointer contains invalid activation metadata")
        entrypoint = pointer.get("entrypoint")
        if not isinstance(entrypoint, str) or not entrypoint.endswith(".py"):
            raise ValueError("Agent release pointer contains an invalid entrypoint")
        release = self._release_path(release_id)
        payload = (release / "payload").resolve()
        target = (payload / Path(*PurePosixPath(entrypoint).parts)).resolve()
        if payload not in target.parents or not _is_regular(target):
            raise ValueError("Agent release pointer escapes its payload")
        if not self._venv_python(release / "venv").is_file():
            raise ValueError("Agent release pointer references a missing virtual environment")

    def _append_history(self, pointer: dict) -> None:
        history = self._read_history()
        normalized = {
            **pointer,
            "activation_execution_id": "",
            "activation_operation": "",
        }
        history = [
            item for item in history
            if item.get("release_id") != normalized.get("release_id")
        ]
        history.append(normalized)
        _atomic_json(
            self.history_path,
            {"schema_version": 1, "releases": history[-20:]},
        )

    def _read_history(self) -> list[dict]:
        if not self.history_path.is_file():
            return []
        try:
            value = _safe_json(self.history_path)
        except ValueError:
            return []
        releases = value.get("releases")
        if value.get("schema_version") != 1 or not isinstance(releases, list):
            return []
        return [item for item in releases if isinstance(item, dict)]

    def _select_rollback_target(
        self,
        current: dict,
        requested_release_id: str,
        requested_version: str,
    ) -> dict | None:
        for candidate in reversed(self._read_history()):
            if candidate.get("release_id") == current.get("release_id"):
                continue
            if (
                candidate.get("release_id") != requested_release_id
                or candidate.get("version") != requested_version
            ):
                continue
            try:
                self._validate_release_pointer(candidate)
            except ValueError:
                continue
            return candidate
        return None

    def _prune_releases(self) -> None:
        active = self._read_active(required=False)
        keep: list[str] = []
        if active.get("release_id"):
            keep.append(str(active["release_id"]))
        for pointer in reversed(self._read_history()):
            release_id = str(pointer.get("release_id") or "")
            if release_id and release_id not in keep:
                keep.append(release_id)
            if len(keep) >= 3:
                break
        if self.pending_path.is_file():
            try:
                pending = _safe_json(self.pending_path)
            except ValueError:
                pending = {}
            for key in ("previous", "target"):
                release_id = str((pending.get(key) or {}).get("release_id") or "")
                if release_id and release_id not in keep:
                    keep.append(release_id)
        for child in self.releases_root.iterdir():
            if child.name.startswith(".staging-"):
                continue
            if child.name not in keep:
                _safe_rmtree(child, self.releases_root)

    def _claim_request(self) -> tuple[str, Path] | None:
        requests = self.spool_root / "requests"
        processing = self.spool_root / "processing"
        for request_path in sorted(requests.glob("*.json")):
            execution_id = request_path.stem
            if not UPDATE_ID_RE.fullmatch(execution_id):
                continue
            processing_path = processing / request_path.name
            try:
                os.replace(request_path, processing_path)
            except OSError:
                continue
            return execution_id, processing_path
        return None

    @staticmethod
    def _load_request(path: Path, execution_id: str) -> dict:
        request = _safe_json(path)
        action = request.get("action")
        if action == "activate":
            expected = {
                "schema_version",
                "action",
                "execution_id",
                "release_id",
                "package_sha256",
                "package_size",
                "deployment_id",
                "health_timeout",
                "created_at",
            }
            if set(request) != expected:
                raise ValueError("activation request schema is invalid")
            if (
                request.get("schema_version") != 1
                or request.get("execution_id") != execution_id
                or not RELEASE_ID_RE.fullmatch(str(request.get("release_id") or ""))
                or not re_full_sha256(request.get("package_sha256"))
                or not isinstance(request.get("package_size"), int)
                or not 0 < request["package_size"] <= MAX_PACKAGE_BYTES
                or (
                    request.get("deployment_id")
                    and not UPDATE_ID_RE.fullmatch(str(request["deployment_id"]))
                )
                or request.get("health_timeout") != HEALTH_TIMEOUT_SECONDS
            ):
                raise ValueError("activation request fields are invalid")
        elif action == "rollback":
            expected = {
                "schema_version",
                "action",
                "execution_id",
                "source_execution_id",
                "deployment_id",
                "target_release_id",
                "target_version",
                "health_timeout",
                "created_at",
            }
            if set(request) != expected:
                raise ValueError("rollback request schema is invalid")
            source = str(request.get("source_execution_id") or "")
            if (
                request.get("schema_version") != 1
                or request.get("execution_id") != execution_id
                or (source and not UPDATE_ID_RE.fullmatch(source))
                or (
                    request.get("deployment_id")
                    and not UPDATE_ID_RE.fullmatch(str(request["deployment_id"]))
                )
                or not RELEASE_ID_RE.fullmatch(
                    str(request.get("target_release_id") or "")
                )
                or not isinstance(request.get("target_version"), str)
                or not VERSION_RE.fullmatch(request["target_version"])
                or request.get("health_timeout") != HEALTH_TIMEOUT_SECONDS
            ):
                raise ValueError("rollback request fields are invalid")
        else:
            raise ValueError("unsupported privileged update action")
        return request

    @staticmethod
    def _validate_pending(value: dict) -> None:
        expected = {
            "schema_version",
            "execution_id",
            "action",
            "previous",
            "target",
            "deadline",
        }
        if (
            set(value) != expected
            or value.get("schema_version") != 1
            or value.get("action") not in {"activate", "rollback"}
            or not UPDATE_ID_RE.fullmatch(str(value.get("execution_id") or ""))
            or not isinstance(value.get("previous"), dict)
            or not isinstance(value.get("target"), dict)
            or not isinstance(value.get("deadline"), int)
        ):
            raise ValueError("pending activation state is invalid")

    def _ensure_layout(self) -> None:
        self.install_root.mkdir(parents=True, exist_ok=True)
        self.releases_root.mkdir(mode=0o755, exist_ok=True)
        self.control_root.mkdir(mode=0o700, exist_ok=True)
        self.spool_root.mkdir(parents=True, exist_ok=True)
        spool_info = self.spool_root.stat()
        shared_names = (
            "incoming",
            "packages",
            "requests",
            "agent-status",
            "health",
            "cancel",
        )
        helper_names = ("processing", "helper-status")
        for name in (*shared_names, *helper_names):
            path = self.spool_root / name
            path.mkdir(mode=0o750, exist_ok=True)
            if os.name != "nt":
                try:
                    path_info = path.stat()
                    if name in shared_names:
                        # Preserve the unprivileged Agent owner installed by
                        # install.sh, while pinning the trusted group.  Group
                        # write also makes a safely created missing directory
                        # usable without guessing the service account UID.
                        os.chown(path, path_info.st_uid, spool_info.st_gid)
                        path.chmod(0o770)
                    else:
                        # Helper-owned state is readable but never writable by
                        # the Agent service account.
                        os.chown(path, os.geteuid(), spool_info.st_gid)
                        path.chmod(0o750)
                except PermissionError:
                    pass
        for root in (self.install_root, self.releases_root, self.control_root, self.spool_root):
            if root.is_symlink():
                raise ValueError("updater roots cannot be symbolic links")

    def _release_path(self, release_id: str) -> Path:
        if not RELEASE_ID_RE.fullmatch(release_id):
            raise ValueError("invalid release ID")
        root = self.releases_root.resolve()
        path = (root / release_id).resolve()
        if root not in path.parents:
            raise ValueError("release path escapes its root")
        return path

    @staticmethod
    def _ensure_safe_parent(root: Path, parent: Path) -> None:
        root_resolved = root.resolve()
        relative = parent.relative_to(root)
        current = root
        for component in relative.parts:
            current = current / component
            if current.exists():
                if current.is_symlink() or not current.is_dir():
                    raise ValueError("release payload parent is unsafe")
            else:
                current.mkdir(mode=0o755)
            if root_resolved not in current.resolve().parents:
                raise ValueError("release payload path escapes its root")

    @staticmethod
    def _venv_python(virtualenv: Path) -> Path:
        if os.name == "nt":
            return virtualenv / "Scripts" / "python.exe"
        return virtualenv / "bin" / "python"

    @staticmethod
    def _open_no_follow(path: Path) -> int:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        return os.open(path, flags)

    def _write_status(
        self,
        execution_id: str,
        stage: str,
        *,
        progress: int,
        **fields: object,
    ) -> None:
        _atomic_json(self._status_path(execution_id), {
            "schema_version": 1,
            "execution_id": execution_id,
            "stage": stage,
            "progress": max(0, min(100, int(progress))),
            "updated_at": int(self.now()),
            **fields,
        }, mode=0o644)

    def _status_path(self, execution_id: str) -> Path:
        return self.spool_root / "helper-status" / f"{execution_id}.json"

    def _health_path(self, execution_id: str) -> Path:
        return self.spool_root / "health" / f"{execution_id}.json"

    def _cancel_path(self, execution_id: str) -> Path:
        return self.spool_root / "cancel" / f"{execution_id}.json"

    def _cleanup_staged_package(self, execution_id: str) -> None:
        (self.spool_root / "packages" / f"{execution_id}.wcmupd").unlink(
            missing_ok=True
        )
        (self.spool_root / "packages" / f"{execution_id}.json").unlink(
            missing_ok=True
        )

    def _restart_agent_service(self) -> None:
        if os.name == "nt":
            subprocess.run(
                ["schtasks.exe", "/End", "/TN", "WebClusterAgent"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
            result = subprocess.run(
                ["schtasks.exe", "/Run", "/TN", "WebClusterAgent"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        else:
            result = subprocess.run(
                ["systemctl", "restart", "web-cluster-agent.service"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                timeout=30,
            )
        if result.returncode != 0:
            raise RuntimeError("Agent service restart command failed")

    def _sanitize_error(self, error: Exception) -> str:
        text = str(error).replace("\r", " ").replace("\n", " ")
        for path in (
            self.install_root,
            self.spool_root,
            self.trusted_keys_dir,
        ):
            text = text.replace(str(path), "<local-path>")
        return text[:500] or "privileged update failed"


def re_full_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    return all(character in "0123456789abcdef" for character in value)


def default_paths() -> tuple[Path, Path, Path]:
    if os.name == "nt":
        install = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "WebClusterAgent"
        data = Path(os.getenv("ProgramData", r"C:\ProgramData")) / "WebClusterAgent"
    else:
        install = Path("/opt/web-cluster-agent")
        data = Path("/var/lib/web-cluster-agent")
    return install, data / "update-spool", install / "trusted_update_keys"


def main() -> int:
    install, spool, keys = default_paths()
    parser = argparse.ArgumentParser(description="Privileged Web Cluster Agent update helper")
    parser.add_argument("--install-root", default=str(install))
    parser.add_argument("--spool-root", default=str(spool))
    parser.add_argument("--trusted-keys", default=str(keys))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    helper = UpdateHelper(
        Path(args.install_root),
        Path(args.spool_root),
        Path(args.trusted_keys),
    )
    try:
        if args.once:
            helper.process_once()
        else:
            helper.run_forever()
        return 0
    except KeyboardInterrupt:
        return 0
    except Exception:
        # The service manager records a generic failure without package paths,
        # manifest content, credentials, or request-controlled strings.
        print("privileged Agent update helper failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
