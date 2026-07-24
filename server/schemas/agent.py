from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EnrollmentTokenCreate(BaseModel):
    label: str | None = Field(default=None, max_length=200)
    ttl_minutes: int = Field(default=15, ge=1, le=1440)


class EnrollmentTokenResponse(BaseModel):
    id: str
    token: str
    expires_at: datetime


class EnrollmentTokenInfo(BaseModel):
    id: str
    label: str | None
    created: datetime
    expires_at: datetime
    used_at: datetime | None
    revoked_at: datetime | None


class AgentEnrollRequest(BaseModel):
    agent_id: str = Field(min_length=32, max_length=64)
    ip: str = Field(min_length=1, max_length=45)
    hostname: str = Field(min_length=1, max_length=255)
    os: str = Field(min_length=1, max_length=255)
    platform: Literal["windows", "linux", "docker-host", "unknown"] = "unknown"
    version: str = Field(default="0.0.0", max_length=20)
    capabilities: dict = Field(default_factory=dict)


class AgentEnrollResponse(BaseModel):
    node_id: str
    agent_token: str


class AgentCredentialResponse(BaseModel):
    node_id: str
    agent_token: str


class ContainerActionRequest(BaseModel):
    action: Literal["start", "stop", "restart"]


class ContainerResponse(BaseModel):
    id: str
    host_node_id: str
    runtime_id: str
    name: str
    image: str | None = None
    state: str
    status: str | None = None
    cpu_percent: float | None = None
    mem_percent: float | None = None
    mem_usage: str | None = None
    last_seen: datetime | None = None

    model_config = {"from_attributes": True}
