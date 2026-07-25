from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator


TaskType = Literal[
    "health_check",
    "clean_logs",
    "backup_files",
    "restart_service",
    "batch_command",
]

TaskStatus = Literal[
    "queued",
    "running",
    "paused",
    "cancel_requested",
    "completed",
    "partial",
    "failed",
    "cancelled",
]


class TaskTargets(BaseModel):
    target_node_ids: list[str] = Field(default_factory=list, max_length=32)
    target_group_ids: list[str] = Field(default_factory=list, max_length=32)
    all_online: bool = False

    @field_validator("target_node_ids", "target_group_ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("目标 ID 无效")
        return value


class TaskCreate(TaskTargets):
    type: TaskType
    title: str | None = Field(default=None, max_length=255)
    params: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_params(self):
        params = self.params
        expected: set[str]
        if self.type == "health_check":
            expected = set()
        elif self.type == "clean_logs":
            expected = {"profile", "older_than_days", "dry_run", "preview_task_id"}
            required = {"profile", "older_than_days", "dry_run"}
            if not required.issubset(params):
                raise ValueError("日志清理缺少 profile、older_than_days 或 dry_run")
            if not isinstance(params["dry_run"], bool):
                raise ValueError("dry_run 必须是布尔值")
            days = params["older_than_days"]
            if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= 3650:
                raise ValueError("older_than_days 必须在 1 到 3650 之间")
            if not params["dry_run"] and not params.get("preview_task_id"):
                raise ValueError("正式日志清理必须引用 preview_task_id")
        elif self.type == "backup_files":
            expected = {"profile", "source"}
            if not expected.issubset(params):
                raise ValueError("文件备份缺少 profile 或 source")
        elif self.type in {"restart_service", "batch_command"}:
            expected = {"profile"}
            if "profile" not in params:
                raise ValueError("任务缺少 profile")
        else:  # pragma: no cover - guarded by Literal
            raise ValueError("不支持的任务类型")
        unknown = set(params) - expected
        if unknown:
            raise ValueError(f"任务参数不允许字段: {', '.join(sorted(unknown))}")
        profile = params.get("profile")
        if profile is not None and (
            not isinstance(profile, str)
            or not profile
            or len(profile) > 64
            or any(ch not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for ch in profile)
        ):
            raise ValueError("配置档名称无效")
        source = params.get("source")
        if source is not None and (
            not isinstance(source, str) or not source.strip() or len(source) > 500
        ):
            raise ValueError("备份源必须是有效的相对路径")
        return self


class TaskRetry(BaseModel):
    target_node_ids: list[str] | None = Field(default=None, max_length=32)


class ResolvedTaskNode(BaseModel):
    id: str
    hostname: str | None = None
    ip: str
    platform: str
    status: str
    compatible: bool
    incompatibility: str | None = None


class TaskTargetsResponse(BaseModel):
    nodes: list[ResolvedTaskNode]
    common_profiles: dict[str, list[str]]


class SubtaskResponse(BaseModel):
    id: str
    node_id: str
    execution_id: str | None = None
    status: str
    attempts: int
    progress: int
    message: str | None = None
    error: str | None = None
    result: dict | None = None
    started: datetime | None = None
    finished: datetime | None = None


class TaskResponse(BaseModel):
    id: str
    type: str
    title: str | None = None
    status: str
    created_by: str | None = None
    created_by_name: str | None = None
    created: datetime | None = None
    started: datetime | None = None
    updated: datetime | None = None
    finished: datetime | None = None
    subtask_count: int = 0
    completed_count: int = 0
    progress: int = 0

    model_config = {"from_attributes": True}


class TaskDetail(TaskResponse):
    params: dict
    subtasks: list[SubtaskResponse]
