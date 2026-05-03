#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""GUI 标签页基类。"""

import threading
import tkinter as tk
from tkinter import ttk
from typing import Any, Callable


class ServiceContainer:
    """GUI 各标签页共享的服务和状态。"""

    def __init__(self, config: dict, node_manager, network, logger, update_manager):
        self.config = config
        self.node_manager = node_manager
        self.network = network
        self.logger = logger
        self.update_manager = update_manager
        self.selected_node_ip = tk.StringVar()
        self.log_callback: Callable[[str], None] | None = None
        self.refresh_callback: Callable[[], None] | None = None
        self.root: tk.Tk | None = None

        # Service 层（延迟注入）
        self.node_service: Any = None
        self.task_service: Any = None
        self.file_service: Any = None
        self.update_service: Any = None
        self.monitor_service: Any = None
        self.log_service: Any = None

    def log(self, message: str) -> None:
        if self.log_callback:
            self.log_callback(message)

    def refresh_all(self) -> None:
        if self.refresh_callback:
            self.refresh_callback()


class BaseTab:
    """所有标签页的基类。"""

    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer):
        self.services = services
        self.frame = ttk.Frame(notebook)
        notebook.add(self.frame, text=title)
        self._create_widgets()

    def _create_widgets(self) -> None:
        raise NotImplementedError

    def log(self, message: str) -> None:
        self.services.log(message)

    def get_online_nodes(self) -> list[str]:
        return self.services.node_manager.get_online_nodes()

    def get_all_nodes(self) -> dict[str, Any]:
        return self.services.node_manager.get_all_nodes()

    def run_async(self, fn: Callable[[], None]) -> None:
        threading.Thread(target=fn, daemon=True).start()

    def schedule(self, delay_ms: int, fn: Callable[[], None]) -> None:
        if self.services.root:
            self.services.root.after(delay_ms, fn)
