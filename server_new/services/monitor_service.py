#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""监控服务 — 监控启停、数据采集、告警。"""

import time
import threading
from typing import Any, Callable

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger


class MonitorService:
    """性能监控的业务编排层。"""

    def __init__(self, node_manager: NodeManager,
                 network: NetworkManager, logger: Logger) -> None:
        self._nm = node_manager
        self._net = network
        self._log = logger
        self._monitoring: dict[str, bool] = {}
        self._threads: dict[str, threading.Thread] = {}
        self._alert_times: dict[str, float] = {}
        self._data_callback: Callable[[str, dict[str, Any]], None] | None = None
        self._alert_callback: Callable[[str, str], None] | None = None

    def set_data_callback(self, cb: Callable[[str, dict[str, Any]], None]) -> None:
        self._data_callback = cb

    def set_alert_callback(self, cb: Callable[[str, str], None]) -> None:
        self._alert_callback = cb

    def is_monitoring(self, ip: str) -> bool:
        return self._monitoring.get(ip, False)

    def start_monitoring(self, target_ips: list[str]) -> dict[str, Any]:
        """启动对一批节点的监控。"""
        results: dict[str, Any] = {}
        for ip in target_ips:
            if self._monitoring.get(ip):
                results[ip] = {'status': 'skipped', 'message': '已在监控中'}
                continue

            result = self._net.send_command(ip, 'start_monitor', {})
            if result and result.get('status') == 'success':
                self._monitoring[ip] = True
                t = threading.Thread(target=self._collect_loop, args=(ip,), daemon=True)
                t.start()
                self._threads[ip] = t
                self._log.log_operation('启动监控', ip, '监控已启动')
                results[ip] = {'status': 'success'}
            else:
                results[ip] = {'status': 'error', 'message': '启动监控失败'}

        success_count = sum(1 for r in results.values() if r.get('status') == 'success')
        return {
            'status': 'success' if success_count > 0 else 'error',
            'results': results,
            'success_count': success_count,
            'fail_count': len(target_ips) - success_count
        }

    def stop_monitoring(self, target_ips: list[str]) -> None:
        """停止对一批节点的监控。"""
        for ip in target_ips:
            if self._monitoring.get(ip):
                self._monitoring[ip] = False
                self._net.send_command(ip, 'stop_monitor', {})
                self._log.log_operation('停止监控', ip, '监控已停止')
                self._alert_times.pop(ip, None)

    def stop_all(self) -> None:
        """停止所有监控。"""
        for ip in list(self._monitoring.keys()):
            self._monitoring[ip] = False
            self._net.send_command(ip, 'stop_monitor', {})
            self._log.log_operation('停止监控', ip, '监控已停止')
        self._monitoring.clear()
        self._threads.clear()
        self._alert_times.clear()

    def check_alerts(self, ip: str, data: dict[str, Any],
                     cpu_threshold: float, memory_threshold: float) -> list[str]:
        """检查告警阈值，返回告警消息列表。"""
        alerts: list[str] = []
        cpu = data.get('cpu_percent', 0)
        memory = data.get('memory_percent', 0)
        if cpu > cpu_threshold:
            alerts.append(f"CPU使用率过高: {cpu:.2f}% (阈值: {cpu_threshold}%)")
        if memory > memory_threshold:
            alerts.append(f"内存使用率过高: {memory:.2f}% (阈值: {memory_threshold}%)")
        return alerts

    def should_suppress_alert(self, ip: str, interval: float = 60) -> bool:
        """检查是否应抑制重复告警（默认 60 秒内不重复弹窗）。"""
        current_time = time.time()
        if ip in self._alert_times:
            if current_time - self._alert_times[ip] < interval:
                return True
        self._alert_times[ip] = current_time
        return False

    def _collect_loop(self, target_ip: str) -> None:
        while self._monitoring.get(target_ip, False):
            try:
                nodes = self._nm.get_all_nodes()
                if target_ip in nodes and nodes[target_ip].get('monitor'):
                    monitor_data = nodes[target_ip]['monitor']
                    if self._data_callback:
                        self._data_callback(target_ip, monitor_data)
                time.sleep(5)
            except Exception:
                time.sleep(5)
