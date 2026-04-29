#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""文件传输标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import datetime
from pathlib import Path
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class FileTransferTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.update_ip_var: Optional[tk.StringVar] = None
        self.update_ip_combo: Optional[ttk.Combobox] = None
        self.update_file_var: Optional[tk.StringVar] = None
        self.update_remote_var: Optional[tk.StringVar] = None
        self.update_result_text: Optional[scrolledtext.ScrolledText] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        select_frame = ttk.LabelFrame(self.frame, text="选择节点")
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(select_frame, text="目标节点IP:").pack(side=tk.LEFT, padx=5)
        self.update_ip_var = tk.StringVar()
        self.update_ip_combo = ttk.Combobox(select_frame, textvariable=self.update_ip_var, width=18)
        self.update_ip_combo.pack(side=tk.LEFT, padx=5)

        file_frame = ttk.LabelFrame(self.frame, text="选择文件（支持任意类型）")
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        self.update_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.update_file_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="浏览", command=self._browse_file).pack(side=tk.LEFT, padx=5)

        remote_frame = ttk.LabelFrame(self.frame, text="保存文件名（可选，留空则使用原文件名）")
        remote_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(remote_frame, text="文件名:").pack(side=tk.LEFT, padx=5)
        self.update_remote_var = tk.StringVar()
        ttk.Entry(remote_frame, textvariable=self.update_remote_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Label(remote_frame, text="（文件将保存到客户端的 Transfer Files 文件夹）", foreground="gray").pack(side=tk.LEFT, padx=5)

        ttk.Button(self.frame, text="开始传输", command=self._start_transfer).pack(pady=10)

        result_frame = ttk.LabelFrame(self.frame, text="传输结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.update_result_text = scrolledtext.ScrolledText(result_frame, height=10)
        self.update_result_text.pack(fill=tk.BOTH, expand=True)

    def refresh_tab(self) -> None:
        if self.update_ip_combo:
            self.update_ip_combo['values'] = self.get_online_nodes()

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要传输的文件")
        if path:
            self.update_file_var.set(path)
            if not self.update_remote_var.get():
                self.update_remote_var.set(Path(path).name)

    def _start_transfer(self) -> None:
        target_ip = self.update_ip_var.get()
        if not target_ip:
            messagebox.showerror("错误", "请输入目标节点IP")
            return

        file_path = self.update_file_var.get()
        if not file_path:
            messagebox.showerror("错误", "请选择要传输的文件")
            return

        if not Path(file_path).exists():
            messagebox.showerror("错误", "选择的文件不存在")
            return

        remote_path = self.update_remote_var.get().strip()
        if not remote_path:
            remote_path = Path(file_path).name

        self.run_async(lambda: self._do_transfer(target_ip, file_path, remote_path))

    def _do_transfer(self, target_ip: str, file_path: str, remote_path: str) -> None:
        now_str = f"[{datetime.datetime.now()}]"
        self.update_result_text.insert(tk.END, f"{now_str} {target_ip}: 开始传输文件...\n")
        self.update_result_text.see(tk.END)

        result = self.services.file_service.transfer_file(target_ip, file_path, remote_path)

        now_str = f"[{datetime.datetime.now()}]"
        message = result.get('message', str(result))
        status = result.get('status', 'unknown')
        if status == 'success':
            self.update_result_text.insert(tk.END, f"{now_str} {target_ip}: 文件传输成功 - {message}\n")
        else:
            self.update_result_text.insert(tk.END, f"{now_str} {target_ip}: 文件传输失败 - {message}\n")

        self.update_result_text.see(tk.END)
