#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""日志服务 — 操作日志记录和查询。"""

import datetime
from pathlib import Path
from typing import Any

from core.logger import Logger


class LogService:
    """操作日志的业务编排层。"""

    def __init__(self, logger: Logger) -> None:
        self._log = logger

    def log(self, target_ip: str, command: str,
            file_or_folder: str | None = None) -> None:
        self._log.log(target_ip, command, file_or_folder)

    def log_operation(self, operation_type: str, target_ip: str,
                      details: str) -> None:
        self._log.log_operation(operation_type, target_ip, details)

    def clean_log(self, log_date: str, target_ip: str | None = None) -> dict[str, Any]:
        return self._log.clean_log(log_date, target_ip)

    def get_recent_operations(self, target_ip: str,
                              days: int = 7) -> list[dict[str, Any]]:
        """获取最近 N 天的操作记录。"""
        entries: list[dict[str, Any]] = []
        ip_log_dir = self._log.log_dir / target_ip
        if not ip_log_dir.exists():
            return entries

        today = datetime.date.today()
        for day_offset in range(days):
            date = today - datetime.timedelta(days=day_offset)
            log_file = ip_log_dir / f"operation_{date}.txt"
            if log_file.exists():
                with open(log_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        entries.append({'date': str(date), 'content': line.strip()})
        return entries
