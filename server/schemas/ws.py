#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""WebSocket 消息 schemas"""

from datetime import datetime, timezone
from typing import Any
from pydantic import BaseModel, Field


class WSMessage(BaseModel):
    """标准 WebSocket 消息格式"""
    type: str                                        # heartbeat / command / command_result / monitor_data / notification
    payload: dict[str, Any] = Field(default_factory=dict)
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class WSCommand(BaseModel):
    """前端或 agent 发送的命令消息"""
    type: str = "command"
    command: str                                     # get_system_info / execute_command / ...
    params: dict[str, Any] = Field(default_factory=dict)
    request_id: str = ""                             # 用于匹配请求和响应


class WSCommandResult(BaseModel):
    """命令执行结果"""
    type: str = "command_result"
    request_id: str
    status: str                                      # success / error
    result: dict[str, Any] = Field(default_factory=dict)
