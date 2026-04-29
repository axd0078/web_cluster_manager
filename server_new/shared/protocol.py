#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
共享协议定义：端口常量、消息类型、通信工具函数。
"""

import socket
import json
import threading
from typing import Any, Callable, TypedDict

# ── 端口定义 ──────────────────────────────────────────
CLIENT_LISTEN_PORT = 8887
SERVER_COMMAND_PORT = 8888
SERVER_MONITOR_PORT = 8889

# ── 缓冲区大小 ────────────────────────────────────────
STREAM_BUFFER_SIZE = 4096
FILE_BUFFER_SIZE = 131072   # 128KB
LARGE_BUFFER_SIZE = 65536   # 64KB

# ── 超时（秒）─────────────────────────────────────────
CONNECT_TIMEOUT = 10
COMMAND_TIMEOUT = 30
FILE_TRANSFER_TIMEOUT = 300
HEARTBEAT_INTERVAL = 10
MONITOR_INTERVAL = 5
REGISTER_TIMEOUT = 3

# ── 消息类型 ──────────────────────────────────────────
class MsgType:
    REGISTER = "register"
    HEARTBEAT = "heartbeat"
    COMMAND = "command"
    TASK_RESULT = "task_result"
    FILE_UPDATE = "file_update"
    UPDATE = "update"
    MONITOR_DATA = "monitor_data"
    BACKUP_FILE = "backup_file"


# ── JSON 消息类型定义 ──────────────────────────────────

class HeartbeatMessage(TypedDict):
    type: str          # "heartbeat"
    os: str
    info: dict[str, Any]


class RegisterMessage(TypedDict):
    type: str          # "register"
    os: str
    info: dict[str, Any]


class CommandMessage(TypedDict):
    type: str          # "command"
    command: str
    params: dict[str, Any]


class TaskResultMessage(TypedDict):
    type: str          # "task_result"
    result: Any


class FileUpdateMessage(TypedDict):
    type: str          # "file_update"
    update_type: str
    remote_path: str
    file_size: int
    is_zip: bool


class UpdateMessage(TypedDict):
    type: str          # "update"
    version: str
    update_type: str


class MonitorDataMessage(TypedDict):
    type: str          # "monitor_data"
    data: dict[str, Any]


class BackupFileMessage(TypedDict):
    type: str          # "backup_file"
    file_size: int
    folder_name: str


class ResponseMessage(TypedDict, total=False):
    status: str
    message: str
    version: str
    hostname: str
    os: str
    os_version: str
    cpu_count_logical: int
    cpu_count_physical: int
    memory_total: int
    memory_available: int
    python_version: str
    disks: list[dict[str, Any]]
    return_code: int
    stdout: str
    stderr: str
    need_update: bool
    update_type: str
    current_version: str
    files_to_update: list[str]
    files_to_delete: list[str]
    manifest: dict[str, Any]
    files_count: int
    package_path: str
    exe_path: str


# ── 工具函数 ──────────────────────────────────────────

def recv_json(sock: socket.socket, timeout: float | None = None,
              buffer_size: int = STREAM_BUFFER_SIZE) -> dict:
    """从 socket 接收一个完整的 JSON 消息。

    自动处理 TCP 分包——反复接收直到能成功解析出完整 JSON。
    """
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
            raise ConnectionError("连接关闭，未收到完整 JSON 消息")
        data += chunk
        try:
            return json.loads(data.decode('utf-8'))
        except json.JSONDecodeError:
            continue


def send_json(sock: socket.socket, data: dict) -> None:
    """向 socket 发送一个 JSON 消息（sendall 保证完整发送）。"""
    sock.sendall(json.dumps(data).encode('utf-8'))


def broadcast(targets: list[str],
              worker: Callable[[str], Any],
              timeout: float = COMMAND_TIMEOUT) -> dict[str, Any]:
    """向多个目标并发执行 worker 函数。

    worker 签名为 (ip: str) -> result。
    返回 {ip: result} 字典。
    """
    results: dict[str, Any] = {}
    lock = threading.Lock()

    def _do_one(ip: str) -> None:
        result = worker(ip)
        with lock:
            results[ip] = result

    threads = [
        threading.Thread(target=_do_one, args=(ip,), daemon=True)
        for ip in targets
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=timeout)

    return results
