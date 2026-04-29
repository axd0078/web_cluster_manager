#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""远程命令标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import datetime
import platform
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class RemoteCmdTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.remote_ip_var: Optional[tk.StringVar] = None
        self.remote_ip_combo: Optional[ttk.Combobox] = None
        self.remote_cmd_var: Optional[tk.StringVar] = None
        self.remote_timeout_var: Optional[tk.StringVar] = None
        self.remote_result_text: Optional[scrolledtext.ScrolledText] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        target_frame = ttk.LabelFrame(self.frame, text="目标节点")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="节点IP:").pack(side=tk.LEFT, padx=5)
        self.remote_ip_var = tk.StringVar()
        self.remote_ip_combo = ttk.Combobox(target_frame, textvariable=self.remote_ip_var, width=18)
        self.remote_ip_combo.pack(side=tk.LEFT, padx=5)

        ttk.Button(target_frame, text="获取系统信息", command=self._get_remote_info).pack(side=tk.LEFT, padx=10)

        cmd_frame = ttk.LabelFrame(self.frame, text="命令输入")
        cmd_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(cmd_frame, text="命令:").pack(side=tk.LEFT, padx=5)
        self.remote_cmd_var = tk.StringVar()
        ttk.Entry(cmd_frame, textvariable=self.remote_cmd_var, width=50).pack(side=tk.LEFT, padx=5)

        ttk.Label(cmd_frame, text="超时(秒):").pack(side=tk.LEFT, padx=5)
        self.remote_timeout_var = tk.StringVar(value="30")
        ttk.Entry(cmd_frame, textvariable=self.remote_timeout_var, width=5).pack(side=tk.LEFT, padx=5)

        ttk.Button(cmd_frame, text="执行", command=self._execute_cmd).pack(side=tk.LEFT, padx=10)

        quick_frame = ttk.LabelFrame(self.frame, text="快捷命令")
        quick_frame.pack(fill=tk.X, padx=5, pady=5)

        is_win = platform.system() == "Windows"
        ttk.Button(quick_frame, text="查看IP配置",
                   command=lambda: self._quick_cmd("ipconfig" if is_win else "ifconfig")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看磁盘",
                   command=lambda: self._quick_cmd("wmic logicaldisk get size,freespace,caption" if is_win else "df -h")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看进程",
                   command=lambda: self._quick_cmd("tasklist" if is_win else "ps aux")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看网络连接",
                   command=lambda: self._quick_cmd("netstat -an")).pack(side=tk.LEFT, padx=5)

        result_frame = ttk.LabelFrame(self.frame, text="执行结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.remote_result_text = scrolledtext.ScrolledText(result_frame, height=15)
        self.remote_result_text.pack(fill=tk.BOTH, expand=True)

    def refresh_tab(self) -> None:
        if self.remote_ip_combo:
            self.remote_ip_combo['values'] = self.get_online_nodes()

    def _append_result(self, text: str) -> None:
        self.remote_result_text.insert(tk.END, text)
        self.remote_result_text.see(tk.END)

    def _get_remote_info(self) -> None:
        ip = self.remote_ip_var.get().strip()
        if not ip:
            messagebox.showerror("错误", "请输入节点IP")
            return

        self._append_result(f"[{datetime.datetime.now()}] 获取节点 {ip} 的系统信息...\n")

        def do_get():
            result = self.services.network.get_remote_system_info(ip)
            if result and result.get('status') == 'success':
                info = f"═══════════════════════════════════════\n"
                info += f"节点IP: {ip}\n"
                info += f"主机名: {result.get('hostname', 'N/A')}\n"
                info += f"操作系统: {result.get('os', 'N/A')}\n"
                info += f"系统版本: {result.get('os_version', 'N/A')}\n"
                info += f"CPU核心(逻辑): {result.get('cpu_count_logical', 'N/A')}\n"
                info += f"CPU核心(物理): {result.get('cpu_count_physical', 'N/A')}\n"
                info += f"总内存: {result.get('memory_total', 0) / (1024**3):.2f} GB\n"
                info += f"可用内存: {result.get('memory_available', 0) / (1024**3):.2f} GB\n"
                info += f"Python版本: {result.get('python_version', 'N/A')}\n"
                info += f"磁盘信息:\n"
                for disk in result.get('disks', []):
                    info += f"  {disk['mountpoint']}: {disk['used']/(1024**3):.1f}/{disk['total']/(1024**3):.1f} GB ({disk['percent']}%)\n"
                info += "═══════════════════════════════════════\n"
                self._append_result(info)
            else:
                self._append_result(f"[{datetime.datetime.now()}] 获取系统信息失败: {result.get('message', '无响应') if result else '无响应'}\n")

        self.run_async(do_get)

    def _execute_cmd(self) -> None:
        ip = self.remote_ip_var.get().strip()
        if not ip:
            messagebox.showerror("错误", "请输入节点IP")
            return
        cmd = self.remote_cmd_var.get().strip()
        if not cmd:
            messagebox.showerror("错误", "请输入命令")
            return
        try:
            timeout = int(self.remote_timeout_var.get())
        except ValueError:
            timeout = 30

        self._append_result(f"[{datetime.datetime.now()}] 在节点 {ip} 执行命令: {cmd}\n")

        def do_execute():
            result = self.services.network.execute_remote_command(ip, cmd, timeout)
            if result:
                self._append_result(f"返回码: {result.get('return_code', 'N/A')}\n")
                self._append_result(f"状态: {result.get('status', 'N/A')}\n")
                if result.get('stdout'):
                    self._append_result(f"输出:\n{result['stdout']}\n")
                if result.get('stderr'):
                    self._append_result(f"错误:\n{result['stderr']}\n")
                self.services.logger.log_operation('远程命令执行', ip, f"命令: {cmd}, 返回码: {result.get('return_code', 'N/A')}")
            else:
                self._append_result(f"[{datetime.datetime.now()}] 命令执行失败: 无响应\n")

        self.run_async(do_execute)

    def _quick_cmd(self, cmd: str) -> None:
        self.remote_cmd_var.set(cmd)
        self._execute_cmd()
