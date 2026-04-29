#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""客户端更新标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import datetime
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer
from gui.widgets.target_selector import resolve_targets


class ClientUpdateTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.update_source_var: Optional[tk.StringVar] = None
        self.new_version_var: Optional[tk.StringVar] = None
        self.release_notes_var: Optional[tk.StringVar] = None
        self.client_update_mode_var: Optional[tk.StringVar] = None
        self.client_update_ip_var: Optional[tk.StringVar] = None
        self.client_update_ip_combo: Optional[ttk.Combobox] = None
        self.client_update_group_var: Optional[tk.StringVar] = None
        self.client_update_group_combo: Optional[ttk.Combobox] = None
        self.client_update_result_text: Optional[scrolledtext.ScrolledText] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        version_frame = ttk.LabelFrame(self.frame, text="版本信息")
        version_frame.pack(fill=tk.X, padx=5, pady=5)

        version_info = self.services.update_manager.get_version_info()
        ttk.Label(version_frame, text=f"当前版本: {version_info.get('current_version', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Label(version_frame, text=f"发布日期: {version_info.get('release_date', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Label(version_frame, text=f"更新说明: {version_info.get('release_notes', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Button(version_frame, text="刷新", command=self._refresh_version).pack(side=tk.RIGHT, padx=5)

        create_frame = ttk.LabelFrame(self.frame, text="创建更新包")
        create_frame.pack(fill=tk.X, padx=5, pady=5)

        row1 = ttk.Frame(create_frame)
        row1.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(row1, text="客户端源目录:").pack(side=tk.LEFT, padx=5)
        self.update_source_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.update_source_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="浏览", command=self._browse_source).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(create_frame)
        row2.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(row2, text="新版本号:").pack(side=tk.LEFT, padx=5)
        self.new_version_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.new_version_var, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="更新说明:").pack(side=tk.LEFT, padx=5)
        self.release_notes_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.release_notes_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(row2, text="创建更新包", command=self._create_package).pack(side=tk.LEFT, padx=10)

        target_frame = ttk.LabelFrame(self.frame, text="目标节点")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="更新模式:").pack(side=tk.LEFT, padx=5)
        self.client_update_mode_var = tk.StringVar(value="all")
        ttk.Radiobutton(target_frame, text="所有在线节点", variable=self.client_update_mode_var, value="all").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="指定节点", variable=self.client_update_mode_var, value="selected").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="按分组", variable=self.client_update_mode_var, value="group").pack(side=tk.LEFT, padx=10)

        node_select_frame = ttk.Frame(target_frame)
        node_select_frame.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(node_select_frame, text="节点IP:").pack(side=tk.LEFT, padx=5)
        self.client_update_ip_var = tk.StringVar()
        self.client_update_ip_combo = ttk.Combobox(node_select_frame, textvariable=self.client_update_ip_var, width=18)
        self.client_update_ip_combo.pack(side=tk.LEFT, padx=5)
        ttk.Label(node_select_frame, text="或分组:").pack(side=tk.LEFT, padx=5)
        self.client_update_group_var = tk.StringVar()
        self.client_update_group_combo = ttk.Combobox(node_select_frame, textvariable=self.client_update_group_var, width=15, state="readonly")
        self.client_update_group_combo.pack(side=tk.LEFT, padx=5)

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="检查客户端版本", command=self._check_versions).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="推送更新", command=self._push_update).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="增量更新（智能）", command=self._smart_update).pack(side=tk.LEFT, padx=5)

        result_frame = ttk.LabelFrame(self.frame, text="更新结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.client_update_result_text = scrolledtext.ScrolledText(result_frame, height=12)
        self.client_update_result_text.pack(fill=tk.BOTH, expand=True)

    def refresh_tab(self) -> None:
        if self.client_update_ip_combo:
            self.client_update_ip_combo['values'] = self.get_online_nodes()
        if self.client_update_group_combo:
            self.client_update_group_combo['values'] = list(self.services.node_manager.get_all_groups().keys())

    def _append_result(self, text: str) -> None:
        self.client_update_result_text.insert(tk.END, text)
        self.client_update_result_text.see(tk.END)

    def _refresh_version(self) -> None:
        version_info = self.services.update_manager.get_version_info()
        self._append_result(f"当前版本: {version_info.get('current_version', 'N/A')}\n")
        self._append_result(f"发布日期: {version_info.get('release_date', 'N/A')}\n")
        self._append_result(f"更新说明: {version_info.get('release_notes', 'N/A')}\n")
        self._append_result(f"文件数量: {len(version_info.get('files', {}))}\n")

    def _browse_source(self) -> None:
        path = filedialog.askdirectory(title="选择客户端源目录")
        if path:
            self.update_source_var.set(path)

    def _create_package(self) -> None:
        source_dir = self.update_source_var.get()
        if not source_dir:
            messagebox.showerror("错误", "请选择客户端源目录")
            return
        new_version = self.new_version_var.get().strip()
        if not new_version:
            messagebox.showerror("错误", "请输入新版本号")
            return
        release_notes = self.release_notes_var.get().strip()

        self._append_result(f"[{datetime.datetime.now()}] 正在创建更新包 v{new_version}...\n")

        def do_create():
            result = self.services.update_manager.create_update_package(source_dir, new_version, release_notes)
            if result['status'] == 'success':
                self._append_result(f"[{datetime.datetime.now()}] {result['message']}\n")
                self._append_result(f"  文件数量: {result.get('files_count', 0)}\n")
                self._append_result(f"  ZIP包: {result.get('package_path', 'N/A')}\n")
                if result.get('exe_path'):
                    self._append_result(f"  EXE文件: {result.get('exe_path')}\n")
            else:
                self._append_result(f"[{datetime.datetime.now()}] 错误: {result['message']}\n")

        self.run_async(do_create)

    def _check_versions(self) -> None:
        online_nodes = self.get_online_nodes()
        if not online_nodes:
            messagebox.showinfo("提示", "没有在线节点")
            return

        self._append_result(f"[{datetime.datetime.now()}] 正在检查客户端版本...\n")

        def do_check():
            result = self.services.update_service.check_client_versions(online_nodes)
            self._append_result(f"服务端当前版本: {result['current_version']}\n")
            self._append_result("-" * 50 + "\n")
            for ip, info in result['results'].items():
                if 'error' in info:
                    self._append_result(f"  {ip}: 无法获取版本\n")
                else:
                    status = "最新" if info['is_latest'] else "需要更新"
                    self._append_result(f"  {ip}: v{info['version']} [{status}]\n")

        self.run_async(do_check)

    def _get_targets(self) -> tuple[list[str], str | None]:
        return resolve_targets(
            self.client_update_mode_var.get(),
            self.client_update_ip_var.get().strip(),
            self.client_update_group_var.get().strip(),
            self.services.node_manager
        )

    def _push_update(self) -> None:
        target_ips, error = self._get_targets()
        if error:
            messagebox.showerror("错误", error)
            return
        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        self._append_result(f"[{datetime.datetime.now()}] 开始推送更新到 {len(target_ips)} 个节点...\n")

        def do_push():
            result = self.services.update_service.push_full_update(target_ips)
            if result.get('status') == 'error':
                self._append_result(f"[{datetime.datetime.now()}] 错误: {result.get('message')}\n")
                return
            for ip, r in result['results'].items():
                if r and r.get('status') == 'success':
                    self._append_result(f"[{datetime.datetime.now()}] {ip}: 更新成功\n")
                else:
                    msg = r.get('message', '未知错误') if r else '无响应'
                    self._append_result(f"[{datetime.datetime.now()}] {ip}: 更新失败 - {msg}\n")
            self._append_result(f"[{datetime.datetime.now()}] 推送完成: 成功 {result['success_count']}, 失败 {result['fail_count']}\n")

        self.run_async(do_push)

    def _smart_update(self) -> None:
        target_ips, error = self._get_targets()
        if error:
            messagebox.showerror("错误", error)
            return
        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        self._append_result(f"[{datetime.datetime.now()}] 开始智能增量更新...\n")

        def do_smart():
            result = self.services.update_service.push_smart_update(target_ips)
            for ip, r in result['results'].items():
                if r.get('status') == 'success':
                    self._append_result(f"[{datetime.datetime.now()}] {ip}: {r.get('message', '更新成功')}\n")
                else:
                    self._append_result(f"[{datetime.datetime.now()}] {ip}: 失败 - {r.get('message', '未知错误')}\n")
            self._append_result(f"[{datetime.datetime.now()}] 智能增量更新完成: 成功 {result['success_count']}, 失败 {result['fail_count']}\n")

        self.run_async(do_smart)
