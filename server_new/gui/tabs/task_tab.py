#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""任务管理标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import datetime
from pathlib import Path
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class TaskTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.task_ip_var: Optional[tk.StringVar] = None
        self.task_ip_combo: Optional[ttk.Combobox] = None
        self.task_type_var: Optional[tk.StringVar] = None
        self.task_date_var: Optional[tk.StringVar] = None
        self.task_param_var: Optional[tk.StringVar] = None
        self.task_result_text: Optional[scrolledtext.ScrolledText] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        select_frame = ttk.LabelFrame(self.frame, text="选择节点")
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(select_frame, text="目标节点IP:").pack(side=tk.LEFT, padx=5)
        self.task_ip_var = tk.StringVar()
        self.task_ip_combo = ttk.Combobox(select_frame, textvariable=self.task_ip_var, width=18)
        self.task_ip_combo.pack(side=tk.LEFT, padx=5)

        task_frame = ttk.LabelFrame(self.frame, text="选择任务")
        task_frame.pack(fill=tk.X, padx=5, pady=5)

        self.task_type_var = tk.StringVar(value="clean_log")
        ttk.Radiobutton(task_frame, text="清理日志", variable=self.task_type_var, value="clean_log").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(task_frame, text="文件备份", variable=self.task_type_var, value="backup").pack(side=tk.LEFT, padx=10)

        param_frame = ttk.LabelFrame(self.frame, text="任务参数")
        param_frame.pack(fill=tk.X, padx=5, pady=5)

        clean_log_frame = ttk.Frame(param_frame)
        clean_log_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(clean_log_frame, text="日期:").pack(side=tk.LEFT, padx=5)
        self.task_date_var = tk.StringVar(value=datetime.datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(clean_log_frame, textvariable=self.task_date_var, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(clean_log_frame, text="今天", command=lambda: self.task_date_var.set(datetime.datetime.now().strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Button(clean_log_frame, text="昨天", command=lambda: self.task_date_var.set((datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Button(clean_log_frame, text="前天", command=lambda: self.task_date_var.set((datetime.datetime.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Label(clean_log_frame, text="（将删除客户端和服务端指定日期的日志）", foreground="gray").pack(side=tk.LEFT, padx=5)

        backup_frame = ttk.Frame(param_frame)
        backup_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(backup_frame, text="保存路径:").pack(side=tk.LEFT, padx=5)
        self.task_param_var = tk.StringVar()
        ttk.Entry(backup_frame, textvariable=self.task_param_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(backup_frame, text="选择文件夹", command=self._browse_backup_folder).pack(side=tk.LEFT, padx=5)
        ttk.Label(backup_frame, text="（客户端将压缩整个文件夹并发送到服务端）", foreground="gray").pack(side=tk.LEFT, padx=5)

        ttk.Button(self.frame, text="执行任务", command=self._execute_task).pack(pady=10)

        result_frame = ttk.LabelFrame(self.frame, text="执行结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.task_result_text = scrolledtext.ScrolledText(result_frame, height=10)
        self.task_result_text.pack(fill=tk.BOTH, expand=True)

    def refresh_tab(self) -> None:
        if self.task_ip_combo:
            self.task_ip_combo['values'] = self.get_online_nodes()

    def _browse_backup_folder(self) -> None:
        path = filedialog.askdirectory(title="选择备份保存文件夹")
        if path:
            self.task_param_var.set(path)

    def _execute_task(self) -> None:
        target_ip = self.task_ip_var.get()
        if not target_ip:
            messagebox.showerror("错误", "请输入目标节点IP")
            return

        task_type = self.task_type_var.get()

        if task_type == 'clean_log':
            log_date = self.task_date_var.get().strip()
            if not log_date:
                messagebox.showerror("错误", "请输入日期 (格式: YYYY-MM-DD)")
                return
            try:
                datetime.datetime.strptime(log_date, '%Y-%m-%d')
            except ValueError:
                messagebox.showerror("错误", "日期格式错误，请使用 YYYY-MM-DD 格式")
                return

            result = self.services.task_service.clean_log(target_ip, log_date)
            server_msg = result['server_result'].get('message', '')
            self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 服务端: {server_msg}\n")

            client_result = result.get('client_result')
            if client_result:
                self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 客户端 {target_ip}: {client_result.get('message', '')}\n")
            else:
                self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 客户端 {target_ip}: 执行失败\n")
        else:
            if task_type == 'backup':
                save_path = self.task_param_var.get().strip()
                if not save_path:
                    messagebox.showerror("错误", "请选择保存路径")
                    return

                result = self.services.task_service.start_backup(target_ip, save_path)

                if result and result.get('status') == 'success':
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 备份命令已发送，等待接收文件...\n")
                    self.task_result_text.see(tk.END)
                    self.schedule(1000, lambda: self._wait_for_backup(target_ip, save_path, 0, 60))
                else:
                    error_msg = result.get('message', '未知错误') if result else '未收到响应'
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 执行失败 - {error_msg}\n")
                    self.task_result_text.see(tk.END)

    def _wait_for_backup(self, target_ip: str, save_path: str, waited: int, max_wait: int) -> None:
        if waited >= max_wait:
            self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 等待备份文件超时（{max_wait}秒）\n")
            self.task_result_text.see(tk.END)
            return

        if self.services.task_service.has_pending_backup(target_ip):
            try:
                result = self.services.task_service.save_backup_file(target_ip, save_path)
                if result.get('status') == 'success':
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 备份文件已保存到 {result['path']}\n")
                else:
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: {result.get('message', '保存失败')}\n")
                self.task_result_text.see(tk.END)
            except Exception as e:
                self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 保存备份文件失败: {e}\n")
                self.task_result_text.see(tk.END)
            return

        self.schedule(1000, lambda: self._wait_for_backup(target_ip, save_path, waited + 1, max_wait))
