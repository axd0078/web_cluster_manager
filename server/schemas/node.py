#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点相关 Pydantic schemas"""

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


class NodeCreate(BaseModel):
    """Agent 注册时发送的节点信息"""
    ip: str
    hostname: str = ""
    os: str = ""
    version: str = "1.0.0"
    tags: dict[str, Any] = Field(default_factory=dict)
    metadata_: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class NodeResponse(BaseModel):
    id: str
    ip: str
    hostname: str
    os: str
    status: str
    version: str
    tags: dict[str, Any]
    metadata_: dict[str, Any]
    last_seen: datetime
    registered: datetime

    model_config = {"from_attributes": True, "populate_by_name": True}


class NodeList(BaseModel):
    total: int
    online: int
    offline: int
    nodes: list[NodeResponse]


class NodeHeartbeat(BaseModel):
    """心跳消息"""
    type: str = "heartbeat"
    os: str = ""
    hostname: str = ""
    version: str = "1.0.0"
    cpu_percent: float = 0.0
    memory_percent: float = 0.0
    disk_percent: float = 0.0
