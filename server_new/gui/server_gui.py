#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import tkinter as tk
from tkinter import ttk
import datetime
from pathlib import Path
from typing import Any

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger
from core.update_manager import UpdateManager
from gui.base_tab import ServiceContainer
from gui.tabs.node_tab import NodeTab
from gui.tabs.group_tab import GroupTab
from gui.tabs.task_tab import TaskTab
from gui.tabs.file_transfer_tab import FileTransferTab
from gui.tabs.client_update_tab import ClientUpdateTab
from gui.tabs.batch_tab import BatchTab
from gui.tabs.remote_cmd_tab import RemoteCmdTab
from gui.tabs.monitor_tab import MonitorTab
from gui.tabs.log_tab import LogTab
from services.node_service import NodeService
from services.task_service import TaskService
from services.file_service import FileService
from services.update_service import UpdateService
from services.monitor_service import MonitorService
from services.log_service import LogService


class ServerGUI:
    """服务端 GUI 主窗口——负责 Notebook 骨架、Tab 注册、定时器和跨 Tab 协调。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Web集群管理服务端")
        self.root.geometry("1200x800")

        config_path = Path(__file__).parent.parent / 'config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        self.node_manager = NodeManager()
        self.logger = Logger(Path(__file__).parent.parent / 'logs')
        self.update_manager = UpdateManager()

        self.network = NetworkManager(
            self.config['server']['command_port'],
            self.config['server']['monitor_port'],
            self.node_manager,
            self._log_message
        )

        self.services = ServiceContainer(
            self.config, self.node_manager, self.network,
            self.logger, self.update_manager
        )
        self.services.root = root
        self.services.log_callback = self._log_message
        self.services.refresh_callback = self._refresh_all_tabs

        # 创建 Service 层实例并注入到容器
        self.services.node_service = NodeService(self.node_manager)
        self.services.task_service = TaskService(self.node_manager, self.network, self.logger)
        self.services.file_service = FileService(self.node_manager, self.network, self.logger)
        self.services.update_service = UpdateService(self.node_manager, self.network, self.update_manager)
        self.services.monitor_service = MonitorService(self.node_manager, self.network, self.logger)
        self.services.log_service = LogService(self.logger)

        self._create_ui()

        self.network.start()
        self._start_timers()
        self._refresh_monitor_nodes()

    # ── UI 构建 ────────────────────────────────────────

    def _create_ui(self) -> None:
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.node_tab = NodeTab(self.notebook, "节点管理", self.services)
        self.group_tab = GroupTab(self.notebook, "节点分组", self.services)
        self.task_tab = TaskTab(self.notebook, "任务管理", self.services)
        self.file_transfer_tab = FileTransferTab(self.notebook, "文件传输", self.services)
        self.client_update_tab = ClientUpdateTab(self.notebook, "客户端更新", self.services)
        self.batch_tab = BatchTab(self.notebook, "批量分发", self.services)
        self.remote_cmd_tab = RemoteCmdTab(self.notebook, "远程命令", self.services)
        self.monitor_tab = MonitorTab(self.notebook, "性能监控", self.services)
        self.log_tab = LogTab(self.notebook, "操作日志", self.services)

        self.node_tab.set_quick_action_callback(self._handle_quick_action)

    def _handle_quick_action(self, action: str, ip: str) -> None:
        tabs = {
            "file_transfer": self.file_transfer_tab,
            "task": self.task_tab,
            "remote_cmd": self.remote_cmd_tab,
            "monitor": self.monitor_tab,
            "add_to_group": self.group_tab,
        }
        tab = tabs.get(action)
        if tab is None:
            return
        if action == "file_transfer":
            self.file_transfer_tab.update_ip_var.set(ip)
        elif action == "task":
            self.task_tab.task_ip_var.set(ip)
        elif action == "remote_cmd":
            self.remote_cmd_tab.remote_ip_var.set(ip)
        elif action == "monitor":
            for i in range(self.monitor_tab.monitor_node_listbox.size()):
                if self.monitor_tab.monitor_node_listbox.get(i).startswith(ip):
                    self.monitor_tab.monitor_node_listbox.selection_set(i)
                    break
        elif action == "add_to_group":
            self.group_tab.add_node_ip_var.set(ip)
        self.notebook.select(self.notebook.index(tab.frame))

    # ── 日志 ───────────────────────────────────────────

    def _log_message(self, message: str) -> None:
        timestamp = f"[{datetime.datetime.now()}]"
        self.log_tab.write_log(f"{timestamp} {message}\n")

    # ── 全局刷新 ──────────────────────────────────────

    def _refresh_all_tabs(self) -> None:
        self.node_tab.refresh_nodes()
        self._refresh_ip_comboboxes()
        self._refresh_groups()
        if hasattr(self, 'monitor_tab'):
            self.monitor_tab.refresh_nodes()

    def _refresh_ip_comboboxes(self) -> None:
        online_nodes = self.node_manager.get_online_nodes()
        all_nodes = list(self.node_manager.get_all_nodes().keys())

        for tab in [self.task_tab, self.file_transfer_tab,
                    self.remote_cmd_tab, self.client_update_tab]:
            if hasattr(tab, 'refresh_tab'):
                tab.refresh_tab()

        if hasattr(self, 'group_tab') and self.group_tab.add_node_ip_combo:
            self.group_tab.add_node_ip_combo['values'] = all_nodes

        if hasattr(self, 'batch_tab'):
            self.batch_tab.refresh_tab()

    def _refresh_groups(self) -> None:
        if not hasattr(self, 'group_tab'):
            return
        self.group_tab.refresh_groups()

        groups = list(self.node_manager.get_all_groups().keys())
        if self.batch_tab.batch_group_combo:
            self.batch_tab.batch_group_combo['values'] = groups
        if self.client_update_tab.client_update_group_combo:
            self.client_update_tab.client_update_group_combo['values'] = groups

    def _refresh_monitor_nodes(self) -> None:
        if hasattr(self, 'monitor_tab'):
            self.monitor_tab.refresh_nodes()

    # ── 定时器 ────────────────────────────────────────

    def _start_timers(self) -> None:
        def refresh():
            self._refresh_all_tabs()
            self.root.after(5000, refresh)
        self.root.after(5000, refresh)


if __name__ == '__main__':
    root = tk.Tk()
    app = ServerGUI(root)
    root.mainloop()
