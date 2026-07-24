from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from schemas.agent import ContainerResponse


# 鈹€鈹€ Node 鈹€鈹€

class NodeRegister(BaseModel):
    ip: str
    hostname: str | None = None
    os: str | None = None
    version: str | None = None


class NodeResponse(BaseModel):
    id: str
    ip: str
    hostname: str | None = None
    os: str | None = None
    platform: str = "unknown"
    credential_state: str = "reenrollment_required"
    status: str
    version: str | None = None
    tags: str = "[]"
    extra_data: str = Field(default="{}")
    last_seen: datetime | None = None
    registered: datetime | None = None
    containers: list[ContainerResponse] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class NodeStatusUpdate(BaseModel):
    status: Literal["online", "offline", "maintenance"]


# 鈹€鈹€ Group 鈹€鈹€

class GroupCreate(BaseModel):
    name: str
    description: str | None = None
    color: str | None = None


class GroupResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    color: str | None = None
    node_count: int = 0

    model_config = {"from_attributes": True}


# 鈹€鈹€ Metric 鈹€鈹€

class MetricSnapshot(BaseModel):
    node_id: str
    cpu_percent: float | None = None
    mem_percent: float | None = None
    mem_used: int | None = None
    mem_total: int | None = None
    disk_percent: float | None = None
    disk_used: int | None = None
    disk_total: int | None = None
    time: datetime | None = None


# 鈹€鈹€ Task 鈹€鈹€

class TaskCreate(BaseModel):
    type: str
    title: str | None = None
    params: dict = Field(default_factory=dict)
    target_node_ids: list[str] = Field(default_factory=list)


class TaskResponse(BaseModel):
    id: str
    type: str
    title: str | None = None
    status: str
    created_by: str | None = None
    created: datetime | None = None
    finished: datetime | None = None
    subtask_count: int = 0
    completed_count: int = 0

    model_config = {"from_attributes": True}


# 鈹€鈹€ Alert 鈹€鈹€

class AlertResponse(BaseModel):
    id: str
    node_id: str | None = None
    severity: str
    message: str
    resolved: bool = False
    created: datetime | None = None

    model_config = {"from_attributes": True}


# 鈹€鈹€ Dashboard 鈹€鈹€

class ClusterHealth(BaseModel):
    total_nodes: int = 0
    online_nodes: int = 0
    offline_nodes: int = 0
    maintenance_nodes: int = 0
    avg_cpu: float | None = None
    avg_memory: float | None = None
    avg_disk: float | None = None
    alerts_active: int = 0
    health_score: int = 100
