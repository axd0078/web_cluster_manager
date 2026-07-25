from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import os
import platform
import re
import shutil
import stat
import time
import uuid
import zipfile
from collections.abc import Awaitable, Callable, Iterator
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath

try:
    from .config import AgentConfig
    from .process_output import (
        ProcessExecutionTimeout,
        ProcessOutputLimitError,
        communicate_bounded,
    )
    from .task_profiles import (
        BackupProfile,
        CommandProfile,
        LogProfile,
        ServiceProfile,
        TaskProfileStore,
    )
except ImportError:  # Script execution from the agent directory.
    from config import AgentConfig
    from process_output import (
        ProcessExecutionTimeout,
        ProcessOutputLimitError,
        communicate_bounded,
    )
    from task_profiles import (
        BackupProfile,
        CommandProfile,
        LogProfile,
        ServiceProfile,
        TaskProfileStore,
    )


SendCallback = Callable[[str, str, dict], Awaitable[None]]


class TaskCancelled(RuntimeError):
    pass


class TaskExecutionError(RuntimeError):
    pass


def _is_link(path: Path) -> bool:
    try:
        if path.is_symlink():
            return True
        is_junction = getattr(path, "is_junction", None)
        return bool(is_junction and is_junction())
    except OSError:
        return True


def _relative_source(root: Path, source: str) -> Path:
    normalized = source.replace("\\", "/")
    windows = PureWindowsPath(source)
    posix = PurePosixPath(normalized)
    if (
        not source
        or "\x00" in source
        or windows.is_absolute()
        or windows.drive
        or posix.is_absolute()
        or normalized.startswith("//")
        or any(part == ".." for part in posix.parts)
    ):
        raise TaskExecutionError("source must be a sandbox-relative path")
    root = root.resolve(strict=True)
    cursor = root
    for part in posix.parts:
        if part in {"", "."}:
            continue
        cursor = cursor / part
        if _is_link(cursor):
            raise TaskExecutionError("symbolic links and junctions are not allowed")
    try:
        resolved = cursor.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise TaskExecutionError("backup source does not exist") from exc
    if not resolved.is_relative_to(root):
        raise TaskExecutionError("backup source escapes the configured root")
    return resolved


class AgentTaskRunner:
    def __init__(
        self,
        config: AgentConfig,
        profiles: TaskProfileStore,
        sender: SendCallback,
    ):
        self.config = config
        self.profiles = profiles
        self.sender = sender
        self.backup_root = config.data_dir / "task_backups"
        self._semaphore = asyncio.Semaphore(max(1, min(config.task_concurrency, 16)))
        self._profile_locks: dict[str, asyncio.Lock] = {}
        self._backup_commit_lock = asyncio.Lock()
        self._running: dict[str, tuple[asyncio.Task, asyncio.Event]] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def validate(self, task_type: str, params: dict) -> str | None:
        self.profiles.reload_if_changed()
        profile = params.get("profile")
        if task_type == "health_check":
            return None if not params else "health_check does not accept parameters"
        allowed_params = {
            "clean_logs": {"profile", "older_than_days", "dry_run", "preview_task_id"},
            "backup_files": {"profile", "source"},
            "restart_service": {"profile"},
            "batch_command": {"profile"},
        }
        if task_type not in allowed_params:
            return "unsupported task type"
        if set(params) - allowed_params[task_type]:
            return "task contains unsupported parameters"
        required_params = {
            "clean_logs": {"profile", "older_than_days", "dry_run"},
            "backup_files": {"profile", "source"},
            "restart_service": {"profile"},
            "batch_command": {"profile"},
        }
        if not required_params[task_type].issubset(params):
            return "task is missing required parameters"
        if not isinstance(profile, str) or not profile:
            return "task profile is required"
        if task_type == "clean_logs":
            days = params.get("older_than_days")
            if (
                isinstance(days, bool)
                or not isinstance(days, int)
                or not 1 <= days <= 3650
                or not isinstance(params.get("dry_run"), bool)
            ):
                return "log cleanup parameters are invalid"
            if params["dry_run"] is False and not params.get("preview_task_id"):
                return "formal log cleanup requires preview_task_id"
        if task_type == "backup_files" and (
            not isinstance(params.get("source"), str) or not params["source"]
        ):
            return "backup source is invalid"
        if task_type == "clean_logs" and profile not in self.profiles.log_profiles:
            return "unknown log profile"
        if task_type == "backup_files" and profile not in self.profiles.backup_profiles:
            return "unknown backup profile"
        if task_type == "restart_service" and profile not in self.profiles.service_profiles:
            return "unknown service profile"
        if task_type == "batch_command":
            if not self.config.enable_remote_commands:
                return "Agent remote command switch is disabled"
            if profile not in self.profiles.command_profiles:
                return "unknown command profile"
        return None

    def accept(
        self,
        execution_id: str,
        *,
        task_id: str,
        subtask_id: str,
        task_type: str,
        params: dict,
    ) -> tuple[bool, str | None]:
        error = self.validate_request(
            execution_id,
            task_id=task_id,
            subtask_id=subtask_id,
            task_type=task_type,
            params=params,
        )
        if error:
            return False, error
        cancel_event = asyncio.Event()
        task = asyncio.create_task(self._run(
            execution_id,
            task_id=task_id,
            subtask_id=subtask_id,
            task_type=task_type,
            params=dict(params),
            cancel_event=cancel_event,
        ))
        self._running[execution_id] = (task, cancel_event)
        task.add_done_callback(lambda _task: self._running.pop(execution_id, None))
        return True, None

    def validate_request(
        self,
        execution_id: str,
        *,
        task_id: str,
        subtask_id: str,
        task_type: str,
        params: dict,
    ) -> str | None:
        identifier = re.compile(r"^[A-Za-z0-9-]{1,64}$")
        if not identifier.fullmatch(execution_id):
            return "invalid execution id"
        if not identifier.fullmatch(task_id) or not identifier.fullmatch(subtask_id):
            return "invalid task identifiers"
        if execution_id in self._running:
            return "execution is already running"
        error = self.validate(task_type, params)
        return error

    async def cancel(self, execution_id: str) -> bool:
        running = self._running.get(execution_id)
        if running is None:
            return False
        task, cancel_event = running
        cancel_event.set()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=8)
        except asyncio.TimeoutError:
            return False
        except Exception:
            pass
        return task.done()

    async def shutdown(self) -> None:
        entries = list(self._running.values())
        for _, event in entries:
            event.set()
        if entries:
            await asyncio.gather(*(task for task, _ in entries), return_exceptions=True)

    async def _run(
        self,
        execution_id: str,
        *,
        task_id: str,
        subtask_id: str,
        task_type: str,
        params: dict,
        cancel_event: asyncio.Event,
    ) -> None:
        self._loop = asyncio.get_running_loop()
        try:
            await self._progress(execution_id, 0, "等待本地执行槽位")
            async with self._semaphore:
                self._check_cancel(cancel_event)
                profile_name = str(params.get("profile") or "")
                lock = self._profile_locks.setdefault(profile_name, asyncio.Lock()) if profile_name else None
                if lock:
                    async with lock:
                        result = await self._execute(
                            task_type, params, task_id, subtask_id, cancel_event, execution_id,
                        )
                else:
                    result = await self._execute(
                        task_type, params, task_id, subtask_id, cancel_event, execution_id,
                    )
            await self.sender("task_result", execution_id, {
                "success": True,
                "status": "completed",
                "message": "任务完成",
                "result": result,
            })
        except TaskCancelled:
            await self.sender("task_result", execution_id, {
                "success": False,
                "status": "cancelled",
                "message": "任务已取消",
                "result": {},
            })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await self.sender("task_result", execution_id, {
                "success": False,
                "status": "failed",
                "error": str(exc)[:2000],
                "message": "任务执行失败",
                "result": {},
            })

    async def _execute(
        self,
        task_type: str,
        params: dict,
        task_id: str,
        subtask_id: str,
        cancel_event: asyncio.Event,
        execution_id: str,
    ) -> dict:
        await self._progress(execution_id, 5, "开始执行")
        if task_type == "health_check":
            result = {
                "hostname": platform.node(),
                "os": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
                "machine": platform.machine(),
                "agent_version": self.config.version,
            }
            await self._progress(execution_id, 100, "健康检查完成")
            return result
        if task_type == "clean_logs":
            profile = self.profiles.log_profiles[str(params["profile"])]
            days = params.get("older_than_days")
            if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
                raise TaskExecutionError("older_than_days is invalid")
            if not isinstance(params.get("dry_run"), bool):
                raise TaskExecutionError("dry_run is invalid")
            return await asyncio.to_thread(
                self._clean_logs,
                profile,
                days,
                params["dry_run"],
                cancel_event,
                execution_id,
            )
        if task_type == "backup_files":
            profile = self.profiles.backup_profiles[str(params["profile"])]
            source = params.get("source")
            if not isinstance(source, str):
                raise TaskExecutionError("source is invalid")
            async with self._backup_commit_lock:
                return await asyncio.to_thread(
                    self._backup_files,
                    profile,
                    source,
                    task_id,
                    subtask_id,
                    cancel_event,
                    execution_id,
                )
        if task_type == "restart_service":
            profile = self.profiles.service_profiles[str(params["profile"])]
            return await self._restart_service(profile, cancel_event, execution_id)
        if task_type == "batch_command":
            if not self.config.enable_remote_commands:
                raise TaskExecutionError("Agent remote command switch is disabled")
            profile = self.profiles.command_profiles[str(params["profile"])]
            return await self._run_command(profile, cancel_event, execution_id)
        raise TaskExecutionError("unsupported task type")

    def _emit_from_thread(self, execution_id: str, progress: int, message: str) -> None:
        if self._loop is None:
            return
        self._loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._progress(execution_id, progress, message))
        )

    async def _progress(self, execution_id: str, progress: int, message: str) -> None:
        await self.sender("task_progress", execution_id, {
            "progress": max(0, min(100, progress)),
            "message": message[:500],
        })

    @staticmethod
    def _check_cancel(cancel_event: asyncio.Event) -> None:
        if cancel_event.is_set():
            raise TaskCancelled("task cancelled")

    def _clean_logs(
        self,
        profile: LogProfile,
        older_than_days: int,
        dry_run: bool,
        cancel_event: asyncio.Event,
        execution_id: str,
    ) -> dict:
        try:
            root = profile.root.resolve(strict=True)
        except (FileNotFoundError, OSError) as exc:
            raise TaskExecutionError("log profile root does not exist") from exc
        if not root.is_dir():
            raise TaskExecutionError("log profile root is not a directory")
        cutoff = time.time() - older_than_days * 86400
        candidate_count = 0
        candidate_bytes = 0
        sample_paths: list[str] = []
        deleted_count = 0
        deleted_bytes = 0
        scanned = 0
        for current, dirs, files in os.walk(root, followlinks=False):
            self._check_cancel(cancel_event)
            current_path = Path(current)
            dirs[:] = [name for name in dirs if not _is_link(current_path / name)]
            for name in files:
                self._check_cancel(cancel_event)
                path = current_path / name
                if _is_link(path):
                    continue
                try:
                    info = path.stat(follow_symlinks=False)
                    resolved = path.resolve(strict=True)
                except (FileNotFoundError, OSError):
                    continue
                if not stat.S_ISREG(info.st_mode) or not resolved.is_relative_to(root):
                    continue
                relative = resolved.relative_to(root).as_posix()
                scanned += 1
                if (
                    info.st_mtime < cutoff
                    and any(
                        fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern)
                        for pattern in profile.patterns
                    )
                ):
                    candidate_count += 1
                    candidate_bytes += info.st_size
                    if len(sample_paths) < 20:
                        sample_paths.append(relative)
                    if not dry_run:
                        try:
                            current_info = path.stat(follow_symlinks=False)
                            current_resolved = path.resolve(strict=True)
                            if (
                                not stat.S_ISREG(current_info.st_mode)
                                or _is_link(path)
                                or not current_resolved.is_relative_to(root)
                                or current_info.st_mtime_ns != info.st_mtime_ns
                                or current_info.st_size != info.st_size
                                or current_info.st_mtime >= cutoff
                            ):
                                continue
                            path.unlink()
                        except (FileNotFoundError, PermissionError, OSError):
                            continue
                        deleted_count += 1
                        deleted_bytes += info.st_size
                if scanned % 250 == 0:
                    self._emit_from_thread(
                        execution_id,
                        min(95, 5 + scanned // 250),
                        "扫描日志文件" if dry_run else "扫描并删除已确认的过期日志",
                    )
        self._emit_from_thread(execution_id, 100, "日志预览完成" if dry_run else "日志清理完成")
        return {
            "dry_run": dry_run,
            "candidate_count": candidate_count,
            "candidate_bytes": candidate_bytes,
            "sample_paths": sample_paths,
            "deleted_count": deleted_count,
            "deleted_bytes": deleted_bytes,
        }

    def _walk_backup_entries(self, source: Path) -> Iterator[tuple[Path, str]]:
        if _is_link(source):
            raise TaskExecutionError("symbolic links and junctions are not allowed")
        if source.is_file():
            yield source, source.name
            return
        if not source.is_dir():
            raise TaskExecutionError("backup source is not a regular file or directory")
        parent = source.parent
        for current, dirs, files in os.walk(source, followlinks=False):
            current_path = Path(current)
            for name in list(dirs):
                if _is_link(current_path / name):
                    raise TaskExecutionError("backup source contains a symbolic link or junction")
            for name in files:
                path = current_path / name
                if _is_link(path):
                    raise TaskExecutionError("backup source contains a symbolic link or junction")
                info = path.stat(follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise TaskExecutionError("backup source contains a non-regular file")
                yield path, path.relative_to(parent).as_posix()

    def _cleanup_expired_backups(self) -> None:
        self.backup_root.mkdir(parents=True, exist_ok=True)
        cutoff = time.time() - max(1, self.config.task_backup_retention_days) * 86400
        for path in self.backup_root.glob("*.zip"):
            try:
                if not _is_link(path) and path.is_file() and path.stat().st_mtime < cutoff:
                    path.unlink()
            except OSError:
                continue
        for path in self.backup_root.glob("*.tmp"):
            try:
                if not _is_link(path) and path.is_file() and path.stat().st_mtime < time.time() - 86400:
                    path.unlink()
            except OSError:
                continue

    def _backup_files(
        self,
        profile: BackupProfile,
        source_value: str,
        task_id: str,
        subtask_id: str,
        cancel_event: asyncio.Event,
        execution_id: str,
    ) -> dict:
        source = _relative_source(profile.root, source_value)
        self.backup_root.mkdir(parents=True, exist_ok=True)
        backup_root = self.backup_root.resolve(strict=True)
        if backup_root == source or backup_root.is_relative_to(source):
            raise TaskExecutionError("backup output directory cannot be inside the source")
        self._cleanup_expired_backups()
        usage = sum(
            path.stat().st_size
            for path in self.backup_root.glob("*.zip")
            if path.is_file() and not _is_link(path)
        )
        count = 0
        source_bytes = 0
        for path, _ in self._walk_backup_entries(source):
            self._check_cancel(cancel_event)
            source_bytes += path.stat(follow_symlinks=False).st_size
            count += 1
            if source_bytes + usage > self.config.task_backup_total_bytes:
                raise TaskExecutionError("backup capacity limit would be exceeded")
        disk = shutil.disk_usage(self.backup_root)
        required = source_bytes + max(0, self.config.task_backup_min_free_bytes)
        if disk.free < required:
            raise TaskExecutionError("insufficient free space for backup")

        safe_source = re.sub(r"[^A-Za-z0-9_.-]+", "_", source.name or "root")[:80] or "root"
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ")
        identity = f"{task_id[:8]}_{subtask_id[:8]}_{stamp}_{safe_source}_{uuid.uuid4().hex[:8]}"
        final_path = self.backup_root / f"{identity}.zip"
        temp_path = self.backup_root / f".{identity}.tmp"
        try:
            with zipfile.ZipFile(
                temp_path,
                mode="x",
                compression=zipfile.ZIP_DEFLATED,
                compresslevel=6,
                allowZip64=True,
            ) as archive:
                for index, (path, archive_name) in enumerate(
                    self._walk_backup_entries(source), start=1,
                ):
                    self._check_cancel(cancel_event)
                    if _is_link(path):
                        raise TaskExecutionError("backup source changed to a symbolic link")
                    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                    if hasattr(os, "O_NOFOLLOW"):
                        flags |= os.O_NOFOLLOW
                    descriptor = os.open(path, flags)
                    try:
                        info = os.fstat(descriptor)
                        if not stat.S_ISREG(info.st_mode):
                            raise TaskExecutionError("backup source changed to a non-regular file")
                        date_time = time.localtime(info.st_mtime)[:6]
                        if date_time[0] < 1980:
                            date_time = (1980, 1, 1, 0, 0, 0)
                        zip_info = zipfile.ZipInfo(archive_name, date_time=date_time)
                        zip_info.compress_type = zipfile.ZIP_DEFLATED
                        zip_info.external_attr = (stat.S_IMODE(info.st_mode) & 0xFFFF) << 16
                        with os.fdopen(descriptor, "rb", closefd=False) as source_file:
                            with archive.open(zip_info, mode="w", force_zip64=True) as destination:
                                while True:
                                    self._check_cancel(cancel_event)
                                    chunk = source_file.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    destination.write(chunk)
                    finally:
                        os.close(descriptor)
                    if index % 10 == 0 or index == count:
                        self._emit_from_thread(
                            execution_id,
                            10 + int(index / max(1, count) * 80),
                            "创建备份压缩包",
                        )
            self._check_cancel(cancel_event)
            final_size = temp_path.stat().st_size
            if usage + final_size > self.config.task_backup_total_bytes:
                raise TaskExecutionError("backup capacity limit was exceeded")
            digest = hashlib.sha256()
            with temp_path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    self._check_cancel(cancel_event)
                    digest.update(chunk)
            if final_path.exists():
                raise TaskExecutionError("generated backup name already exists")
            os.replace(temp_path, final_path)
        except Exception:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        self._emit_from_thread(execution_id, 100, "备份完成")
        return {
            "path": final_path.relative_to(self.config.data_dir).as_posix(),
            "size": final_path.stat().st_size,
            "sha256": digest.hexdigest(),
            "file_count": count,
            "source_bytes": source_bytes,
        }

    async def _run_argv(
        self,
        argv: tuple[str, ...] | list[str],
        *,
        timeout: int,
        cancel_event: asyncio.Event,
    ) -> tuple[int, str, str]:
        self._check_cancel(cancel_event)
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        operation = asyncio.create_task(communicate_bounded(
            process,
            timeout=timeout,
            max_output_bytes=64 * 1024,
        ))
        cancellation = asyncio.create_task(cancel_event.wait())
        done, _ = await asyncio.wait(
            {operation, cancellation},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if cancellation in done and cancel_event.is_set():
            operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)
            raise TaskCancelled("task cancelled")
        cancellation.cancel()
        await asyncio.gather(cancellation, return_exceptions=True)
        try:
            stdout, stderr = await operation
        except ProcessExecutionTimeout as exc:
            raise TaskExecutionError("process execution timed out") from exc
        except ProcessOutputLimitError as exc:
            raise TaskExecutionError("process output exceeded 64 KiB") from exc
        encoding = "gbk" if platform.system() == "Windows" else "utf-8"
        return (
            int(process.returncode or 0),
            stdout.decode(encoding, errors="replace")[-10_000:],
            stderr.decode(encoding, errors="replace")[-10_000:],
        )

    async def _restart_service(
        self,
        profile: ServiceProfile,
        cancel_event: asyncio.Event,
        execution_id: str,
    ) -> dict:
        system = platform.system()
        if profile.manager == "systemd":
            if system != "Linux":
                raise TaskExecutionError("systemd service profile requires Linux")
            before_code, before_out, _ = await self._run_argv(
                ("systemctl", "is-active", profile.name),
                timeout=15,
                cancel_event=cancel_event,
            )
            await self._progress(execution_id, 30, "正在重启 systemd 服务")
            code, _, error = await self._run_argv(
                ("systemctl", "restart", profile.name),
                timeout=60,
                cancel_event=cancel_event,
            )
            if code != 0:
                raise TaskExecutionError(f"service restart failed: {error[-500:]}")
            after = "unknown"
            for _ in range(20):
                self._check_cancel(cancel_event)
                code, output, _ = await self._run_argv(
                    ("systemctl", "is-active", profile.name),
                    timeout=10,
                    cancel_event=cancel_event,
                )
                after = output.strip()
                if code == 0 and after == "active":
                    break
                await asyncio.sleep(0.5)
            if after != "active":
                raise TaskExecutionError("service did not become active")
            await self._progress(execution_id, 100, "服务已恢复 active")
            return {
                "manager": "systemd",
                "before": before_out.strip() if before_code == 0 else "inactive",
                "after": after,
            }

        if profile.manager == "windows_service":
            if system != "Windows":
                raise TaskExecutionError("windows_service profile requires Windows")
            _, before, _ = await self._run_argv(
                ("sc.exe", "query", profile.name),
                timeout=15,
                cancel_event=cancel_event,
            )
            was_running = "RUNNING" in before
            if was_running:
                await self._run_argv(
                    ("sc.exe", "stop", profile.name),
                    timeout=30,
                    cancel_event=cancel_event,
                )
                stopped = False
                for _ in range(30):
                    _, output, _ = await self._run_argv(
                        ("sc.exe", "query", profile.name),
                        timeout=10,
                        cancel_event=cancel_event,
                    )
                    if "STOPPED" in output:
                        stopped = True
                        break
                    await asyncio.sleep(0.5)
                if not stopped:
                    raise TaskExecutionError("service did not become STOPPED")
            await self._progress(execution_id, 60, "正在启动 Windows 服务")
            code, _, error = await self._run_argv(
                ("sc.exe", "start", profile.name),
                timeout=30,
                cancel_event=cancel_event,
            )
            if code != 0:
                raise TaskExecutionError(f"service start failed: {error[-500:]}")
            after = ""
            for _ in range(30):
                _, after, _ = await self._run_argv(
                    ("sc.exe", "query", profile.name),
                    timeout=10,
                    cancel_event=cancel_event,
                )
                if "RUNNING" in after:
                    break
                await asyncio.sleep(0.5)
            if "RUNNING" not in after:
                raise TaskExecutionError("service did not become RUNNING")
            await self._progress(execution_id, 100, "Windows 服务已恢复 RUNNING")
            return {
                "manager": "windows_service",
                "before": "running" if was_running else "stopped",
                "after": "running",
            }
        raise TaskExecutionError("unsupported service manager")

    async def _run_command(
        self,
        profile: CommandProfile,
        cancel_event: asyncio.Event,
        execution_id: str,
    ) -> dict:
        await self._progress(execution_id, 20, "执行本地命令别名")
        code, stdout, stderr = await self._run_argv(
            profile.argv,
            timeout=profile.timeout,
            cancel_event=cancel_event,
        )
        await self._progress(execution_id, 100, "命令执行结束")
        if code != 0:
            raise TaskExecutionError(
                f"command alias returned {code}: {(stderr or stdout)[-500:]}"
            )
        return {
            "returncode": code,
            "stdout": stdout,
            "stderr": stderr,
            "successful": code == 0,
        }
