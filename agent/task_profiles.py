from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path


PROFILE_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")


class TaskProfileError(ValueError):
    pass


@dataclass(frozen=True)
class LogProfile:
    root: Path
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class BackupProfile:
    root: Path


@dataclass(frozen=True)
class ServiceProfile:
    manager: str
    name: str


@dataclass(frozen=True)
class CommandProfile:
    argv: tuple[str, ...]
    timeout: int


class TaskProfileStore:
    """Loads locally trusted task aliases without exposing their values to Server."""

    def __init__(self, path: Path):
        self.path = path
        self.log_profiles: dict[str, LogProfile] = {}
        self.backup_profiles: dict[str, BackupProfile] = {}
        self.service_profiles: dict[str, ServiceProfile] = {}
        self.command_profiles: dict[str, CommandProfile] = {}
        self._signature: tuple[int, int] | None = None
        self.last_error: str | None = None
        self.reload(force=True)

    def _path_signature(self) -> tuple[int, int] | None:
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            return None
        return stat.st_mtime_ns, stat.st_size

    def reload_if_changed(self) -> bool:
        signature = self._path_signature()
        if signature == self._signature:
            return False
        self.reload(force=True)
        return True

    def reload(self, *, force: bool = False) -> None:
        signature = self._path_signature()
        if not force and signature == self._signature:
            return
        if signature is None:
            self.log_profiles = {}
            self.backup_profiles = {}
            self.service_profiles = {}
            self.command_profiles = {}
            self._signature = None
            self.last_error = f"task profile file not found: {self.path}"
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            parsed = self._parse(raw)
        except (OSError, json.JSONDecodeError, TaskProfileError) as exc:
            # Fail closed and keep no aliases when a changed file is invalid.
            self.log_profiles = {}
            self.backup_profiles = {}
            self.service_profiles = {}
            self.command_profiles = {}
            self._signature = signature
            self.last_error = str(exc)
            return
        (
            self.log_profiles,
            self.backup_profiles,
            self.service_profiles,
            self.command_profiles,
        ) = parsed
        self._signature = signature
        self.last_error = None

    def capabilities(self) -> dict:
        return {
            "task_protocol": 2,
            "task_profiles": {
                "clean_logs": sorted(self.log_profiles),
                "backup_files": sorted(self.backup_profiles),
                "restart_service": sorted(self.service_profiles),
                "batch_command": sorted(self.command_profiles),
            },
        }

    def _parse(self, raw: object):
        if not isinstance(raw, dict) or raw.get("version") != 1:
            raise TaskProfileError("task profile version must be 1")
        log_profiles = self._parse_logs(raw.get("log_profiles", {}))
        backup_profiles = self._parse_backups(raw.get("backup_profiles", {}))
        service_profiles = self._parse_services(raw.get("service_profiles", {}))
        command_profiles = self._parse_commands(raw.get("command_profiles", {}))
        return log_profiles, backup_profiles, service_profiles, command_profiles

    def _mapping(self, raw: object, section: str) -> dict:
        if not isinstance(raw, dict) or len(raw) > 128:
            raise TaskProfileError(f"{section} must be an object with at most 128 aliases")
        for name in raw:
            if not isinstance(name, str) or not PROFILE_NAME.fullmatch(name):
                raise TaskProfileError(f"invalid profile alias in {section}")
        return raw

    def _root(self, value: object, section: str) -> Path:
        if not isinstance(value, str) or not value or "\x00" in value:
            raise TaskProfileError(f"{section} root must be a path")
        path = Path(os.path.expandvars(os.path.expanduser(value)))
        if not path.is_absolute():
            path = self.path.parent / path
        return path.resolve(strict=False)

    def _parse_logs(self, raw: object) -> dict[str, LogProfile]:
        output: dict[str, LogProfile] = {}
        for name, value in self._mapping(raw, "log_profiles").items():
            if not isinstance(value, dict):
                raise TaskProfileError("log profile must be an object")
            patterns = value.get("patterns")
            if (
                not isinstance(patterns, list)
                or not patterns
                or len(patterns) > 32
                or any(
                    not isinstance(pattern, str)
                    or not pattern
                    or len(pattern) > 200
                    or "\x00" in pattern
                    or Path(pattern).is_absolute()
                    or ".." in Path(pattern.replace("\\", "/")).parts
                    for pattern in patterns
                )
            ):
                raise TaskProfileError("log profile patterns are invalid")
            output[name] = LogProfile(
                root=self._root(value.get("root"), "log profile"),
                patterns=tuple(patterns),
            )
        return output

    def _parse_backups(self, raw: object) -> dict[str, BackupProfile]:
        output: dict[str, BackupProfile] = {}
        for name, value in self._mapping(raw, "backup_profiles").items():
            if not isinstance(value, dict):
                raise TaskProfileError("backup profile must be an object")
            output[name] = BackupProfile(root=self._root(value.get("root"), "backup profile"))
        return output

    def _parse_services(self, raw: object) -> dict[str, ServiceProfile]:
        output: dict[str, ServiceProfile] = {}
        for name, value in self._mapping(raw, "service_profiles").items():
            if not isinstance(value, dict):
                raise TaskProfileError("service profile must be an object")
            manager = value.get("manager")
            service_name = value.get("name")
            if manager not in {"systemd", "windows_service"}:
                raise TaskProfileError("service manager must be systemd or windows_service")
            if (
                not isinstance(service_name, str)
                or not service_name
                or len(service_name) > 200
                or any(ch in service_name for ch in "\r\n\x00")
            ):
                raise TaskProfileError("service name is invalid")
            output[name] = ServiceProfile(manager=manager, name=service_name)
        return output

    def _parse_commands(self, raw: object) -> dict[str, CommandProfile]:
        output: dict[str, CommandProfile] = {}
        for name, value in self._mapping(raw, "command_profiles").items():
            if not isinstance(value, dict):
                raise TaskProfileError("command profile must be an object")
            argv = value.get("argv")
            timeout = value.get("timeout", 30)
            if (
                not isinstance(argv, list)
                or not argv
                or len(argv) > 64
                or any(
                    not isinstance(part, str)
                    or not part
                    or len(part) > 1000
                    or "\x00" in part
                    for part in argv
                )
            ):
                raise TaskProfileError("command argv is invalid")
            if isinstance(timeout, bool) or not isinstance(timeout, int) or not 1 <= timeout <= 300:
                raise TaskProfileError("command timeout must be between 1 and 300 seconds")
            output[name] = CommandProfile(argv=tuple(argv), timeout=timeout)
        return output
