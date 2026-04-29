#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""操作日志标签页。"""

from tkinter import scrolledtext, ttk
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class LogTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.log_text: Optional[scrolledtext.ScrolledText] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        self.log_text = scrolledtext.ScrolledText(self.frame, height=30)
        self.log_text.pack(fill="both", expand=True, padx=5, pady=5)

    def write_log(self, message: str):
        if self.log_text:
            self.log_text.insert("end", message)
            self.log_text.see("end")
