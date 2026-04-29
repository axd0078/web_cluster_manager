#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务服务 — 日志清理、文件备份。"""

import datetime
from pathlib import Path
from typing import Any

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger


class TaskService:
    """任务管理的业务编排层。"""

    def __init__(self, node_manager: NodeManager,
                 network: NetworkManager, logger: Logger) -> None:
        self._nm = node_manager
        self._net = network
        self._log = logger

    def clean_log(self, target_ip: str, log_date: str) -> dict[str, Any]:
        """清理指定日期的日志（服务端 + 客户端）。"""
        server_result = self._log.clean_log(log_date, target_ip)
        client_result = self._net.send_command(target_ip, 'clean_log', {'date': log_date})
        self._log.log(target_ip, 'clean_log', f"日期: {log_date}")
        self._log.log_operation(
            '清理日志', target_ip,
            f"日期: {log_date}, 服务端: {server_result.get('message', '')}, "
            f"客户端: {client_result.get('message', '') if client_result else '失败'}"
        )
        return {
            'status': 'success' if client_result else 'error',
            'server_result': server_result,
            'client_result': client_result
        }

    def start_backup(self, target_ip: str, save_path: str) -> dict[str, Any]:
        """向客户端发送备份命令。"""
        self._log.log(target_ip, 'backup', save_path)
        result = self._net.send_command(target_ip, 'backup', {})
        return result if result else {'status': 'error', 'message': '未收到响应'}

    def save_backup_file(self, target_ip: str, save_path: str) -> dict[str, Any]:
        """将 pending_backups 中的备份数据保存到磁盘。"""
        with self._net.backup_lock:
            if target_ip not in self._net.pending_backups:
                return {'status': 'error', 'message': '没有待处理的备份文件'}

            backup_info = self._net.pending_backups[target_ip]
            save_dir = Path(save_path)
            save_dir.mkdir(parents=True, exist_ok=True)

            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            zip_filename = f"{backup_info['folder_name']}_backup_{timestamp}.zip"
            zip_path = save_dir / zip_filename

            counter = 1
            while zip_path.exists():
                zip_filename = f"{backup_info['folder_name']}_backup_{timestamp}_{counter}.zip"
                zip_path = save_dir / zip_filename
                counter += 1

            with open(zip_path, 'wb') as f:
                f.write(backup_info['data'])

            self._log.log_operation('文件备份', target_ip,
                                    f"保存路径: {zip_path}, 大小: {backup_info['size']} 字节")
            del self._net.pending_backups[target_ip]
            return {'status': 'success', 'path': str(zip_path), 'size': backup_info['size']}

    def has_pending_backup(self, target_ip: str) -> bool:
        with self._net.backup_lock:
            return target_ip in self._net.pending_backups
