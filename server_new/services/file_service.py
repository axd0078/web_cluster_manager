#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文件服务 — 单文件传输、批量分发。"""

from pathlib import Path
from typing import Any

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger


class FileService:
    """文件传输的业务编排层。"""

    def __init__(self, node_manager: NodeManager,
                 network: NetworkManager, logger: Logger) -> None:
        self._nm = node_manager
        self._net = network
        self._log = logger

    def transfer_file(self, target_ip: str, file_path: str,
                      remote_path: str = '') -> dict[str, Any]:
        """向单个节点传输文件。"""
        path = Path(file_path)
        if not remote_path:
            remote_path = path.name

        self._log.log(target_ip, "file_transfer", file_path)
        self._log.log_operation('文件传输', target_ip,
                                f"文件: {file_path}, 保存为: {remote_path}")

        result = self._net.send_file(target_ip, file_path, remote_path)
        if result is None:
            return {'status': 'error', 'message': '未收到响应'}
        if isinstance(result, dict):
            return result
        return {'status': 'error', 'message': str(result)}

    def transfer_file_to_multiple(self, target_ips: list[str],
                                  file_path: str, remote_path: str = '') -> dict[str, Any]:
        """向多个节点批量传输文件。"""
        path = Path(file_path)
        if not remote_path:
            remote_path = path.name

        results = self._net.send_file_to_multiple(target_ips, file_path, remote_path)
        success_count = sum(1 for r in results.values() if r and r.get('status') == 'success')
        fail_count = len(results) - success_count

        self._log.log_operation('批量文件分发', '多个节点',
                                f"文件: {file_path}, 成功: {success_count}, 失败: {fail_count}")
        return {
            'status': 'success' if fail_count == 0 else 'partial',
            'results': results,
            'success_count': success_count,
            'fail_count': fail_count
        }
