#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Agent 精简协议 — 仅包含 task_executor 需要的常量和接口"""

import json
import socket

# ── 缓存区大小 ──
STREAM_BUFFER_SIZE = 4096
FILE_BUFFER_SIZE = 131072
LARGE_BUFFER_SIZE = 65536

# ── 超时 ──
CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 30
FILE_TRANSFER_TIMEOUT = 300

# ── 消息类型 ──
class MsgType:
    HEARTBEAT = "heartbeat"
    COMMAND = "command"
    TASK_RESULT = "task_result"
    FILE_UPDATE = "file_update"
    UPDATE = "update"
    MONITOR_DATA = "monitor_data"
    BACKUP_FILE = "backup_file"
    REGISTER = "register"


# ── 工具函数（task_executor 中 backup_files 引用，Agent 不使用）──

def send_json(sock: socket.socket, data: dict) -> None:
    sock.sendall(json.dumps(data).encode('utf-8'))


def recv_json(sock: socket.socket, timeout: float | None = None,
              buffer_size: int = STREAM_BUFFER_SIZE) -> dict:
    if timeout is not None:
        sock.settimeout(timeout)
    data = b''
    while True:
        chunk = sock.recv(buffer_size)
        if not chunk:
            if data:
                try:
                    return json.loads(data.decode('utf-8'))
                except json.JSONDecodeError:
                    pass
            raise ConnectionError("Connection closed without complete JSON")
        data += chunk
        try:
            return json.loads(data.decode('utf-8'))
        except json.JSONDecodeError:
            continue
