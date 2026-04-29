#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""批量分发标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext, filedialog
import datetime
from pathlib import Path
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer
from gui.widgets.target_selector import resolve_targets


class BatchTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.batch_mode_var: Optional[tk.StringVar] = None
        self.batch_node_listbox: Optional[tk.Listbox] = None
        self.batch_group_var: Optional[tk.StringVar] = None
        self.batch_group_combo: Optional[ttk.Combobox] = None
        self.batch_file_var: Optional[tk.StringVar] = None
        self.batch_remote_var: Optional[tk.StringVar] = None
        self.batch_result_text: Optional[scrolledtext.ScrolledText] = None
        self.batch_selected_label: Optional[ttk.Label] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        target_frame = ttk.LabelFrame(self.frame, text="目标选择")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="分发模式:").pack(side=tk.LEFT, padx=5)
        self.batch_mode_var = tk.StringVar(value="all")
        ttk.Radiobutton(target_frame, text="所有在线节点", variable=self.batch_mode_var, value="all", command=self._on_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="指定节点", variable=self.batch_mode_var, value="selected", command=self._on_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="按分组", variable=self.batch_mode_var, value="group", command=self._on_mode_change).pack(side=tk.LEFT, padx=10)

        select_frame = ttk.LabelFrame(self.frame, text="选择节点或分组")
        select_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        left_select_frame = ttk.Frame(select_frame)
        left_select_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        ttk.Label(left_select_frame, text="可选节点 (按Ctrl多选):").pack(anchor=tk.W)
        list_frame = ttk.Frame(left_select_frame)
        list_frame.pack(fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.batch_node_listbox = tk.Listbox(list_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar.set, height=5)
        self.batch_node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.batch_node_listbox.yview)

        btn_row = ttk.Frame(left_select_frame)
        btn_row.pack(fill=tk.X, pady=2)
        ttk.Button(btn_row, text="全选", command=lambda: self.batch_node_listbox.selection_set(0, tk.END)).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="取消全选", command=lambda: self.batch_node_listbox.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=2)
        self.batch_selected_label = ttk.Label(btn_row, text="已选: 0 个节点", foreground="blue")
        self.batch_selected_label.pack(side=tk.LEFT, padx=10)
        self.batch_node_listbox.bind('<<ListboxSelect>>', self._update_selected_count)

        right_select_frame = ttk.Frame(select_frame)
        right_select_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)

        ttk.Label(right_select_frame, text="选择分组:").pack(anchor=tk.W)
        self.batch_group_var = tk.StringVar()
        self.batch_group_combo = ttk.Combobox(right_select_frame, textvariable=self.batch_group_var, width=15, state="readonly")
        self.batch_group_combo.pack(pady=5)

        file_frame = ttk.LabelFrame(self.frame, text="选择文件")
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        self.batch_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.batch_file_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="浏览", command=self._browse_file).pack(side=tk.LEFT, padx=5)

        remote_frame = ttk.LabelFrame(self.frame, text="保存文件名")
        remote_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(remote_frame, text="文件名:").pack(side=tk.LEFT, padx=5)
        self.batch_remote_var = tk.StringVar()
        ttk.Entry(remote_frame, textvariable=self.batch_remote_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Label(remote_frame, text="（留空则使用原文件名）", foreground="gray").pack(side=tk.LEFT, padx=5)

        ttk.Button(self.frame, text="开始批量分发", command=self._start_batch).pack(pady=10)

        result_frame = ttk.LabelFrame(self.frame, text="分发结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.batch_result_text = scrolledtext.ScrolledText(result_frame, height=8)
        self.batch_result_text.pack(fill=tk.BOTH, expand=True)

    def refresh_tab(self) -> None:
        if not self.batch_node_listbox:
            return
        self.batch_node_listbox.delete(0, tk.END)
        online_nodes = self.get_online_nodes()
        all_nodes = list(self.get_all_nodes().keys())
        for ip in all_nodes:
            status = '在线' if ip in online_nodes else '离线'
            self.batch_node_listbox.insert(tk.END, f"{ip} ({status})")
        if self.batch_group_combo:
            self.batch_group_combo['values'] = list(self.services.node_manager.get_all_groups().keys())

    def _on_mode_change(self) -> None:
        mode = self.batch_mode_var.get()
        if mode == "all":
            self.batch_node_listbox.config(state='disabled')
            self.batch_group_combo.config(state='disabled')
        elif mode == "selected":
            self.batch_node_listbox.config(state='normal')
            self.batch_group_combo.config(state='disabled')
        elif mode == "group":
            self.batch_node_listbox.config(state='disabled')
            self.batch_group_combo.config(state='readonly')

    def _update_selected_count(self, event: tk.Event | None = None) -> None:
        count = len(self.batch_node_listbox.curselection())
        self.batch_selected_label.config(text=f"已选: {count} 个节点")

    def _browse_file(self) -> None:
        path = filedialog.askopenfilename(title="选择要分发的文件")
        if path:
            self.batch_file_var.set(path)
            if not self.batch_remote_var.get():
                self.batch_remote_var.set(Path(path).name)

    def _start_batch(self) -> None:
        file_path = self.batch_file_var.get()
        if not file_path:
            messagebox.showerror("错误", "请选择要分发的文件")
            return
        if not Path(file_path).exists():
            messagebox.showerror("错误", "选择的文件不存在")
            return

        remote_path = self.batch_remote_var.get().strip()
        if not remote_path:
            remote_path = Path(file_path).name

        mode = self.batch_mode_var.get()

        if mode == "selected":
            selected_indices = self.batch_node_listbox.curselection()
            if not selected_indices:
                messagebox.showerror("错误", "请选择目标节点")
                return
            target_ips = []
            for index in selected_indices:
                text = self.batch_node_listbox.get(index)
                target_ips.append(text.split()[0])
        else:
            single_ip = ""
            group_name = self.batch_group_var.get().strip() if mode == "group" else ""
            target_ips, error = resolve_targets(mode, single_ip, group_name, self.services.node_manager)
            if error:
                messagebox.showerror("错误", error)
                return

        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 开始批量分发到 {len(target_ips)} 个节点...\n")
        self.batch_result_text.see(tk.END)

        def do_batch():
            result = self.services.file_service.transfer_file_to_multiple(target_ips, file_path, remote_path)
            for ip, r in result['results'].items():
                if r and r.get('status') == 'success':
                    self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 成功 - {r.get('message', '')}\n")
                else:
                    self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 失败 - {r.get('message', '未知错误') if r else '无响应'}\n")
                self.batch_result_text.see(tk.END)

            self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 批量分发完成: 成功 {result['success_count']}, 失败 {result['fail_count']}\n")
            self.batch_result_text.see(tk.END)

        self.run_async(do_batch)
