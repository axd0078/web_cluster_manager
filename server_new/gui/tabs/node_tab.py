#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点管理标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox
import datetime
from typing import Callable, Optional
from gui.base_tab import BaseTab, ServiceContainer


class NodeTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.node_tree: Optional[ttk.Treeview] = None
        self.node_context_menu: Optional[tk.Menu] = None
        self._quick_action_cb: Optional[Callable[[str, str], None]] = None
        super().__init__(notebook, title, services)

    def set_quick_action_callback(self, cb: Callable[[str, str], None]) -> None:
        self._quick_action_cb = cb

    def _create_widgets(self) -> None:
        tree_frame = ttk.Frame(self.frame)
        tree_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        columns = ('IP', '操作系统', '状态', '最后心跳')
        self.node_tree = ttk.Treeview(tree_frame, columns=columns, show='headings')

        for col in columns:
            self.node_tree.heading(col, text=col)
            self.node_tree.column(col, width=200)

        scrollbar = ttk.Scrollbar(tree_frame, orient=tk.VERTICAL, command=self.node_tree.yview)
        self.node_tree.configure(yscrollcommand=scrollbar.set)

        self.node_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.node_tree.bind('<Double-1>', self._on_double_click)
        self.node_tree.bind('<Button-3>', self._show_context_menu)

        self.node_context_menu = tk.Menu(self.services.root, tearoff=0)
        self.node_context_menu.add_command(label="发送文件", command=lambda: self._quick_action("file_transfer"))
        self.node_context_menu.add_command(label="执行任务", command=lambda: self._quick_action("task"))
        self.node_context_menu.add_command(label="远程命令", command=lambda: self._quick_action("remote_cmd"))
        self.node_context_menu.add_command(label="性能监控", command=lambda: self._quick_action("monitor"))
        self.node_context_menu.add_separator()
        self.node_context_menu.add_command(label="添加到分组", command=lambda: self._quick_action("add_to_group"))

        btn_frame = ttk.Frame(self.frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="刷新节点列表", command=self.services.refresh_all).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="探测节点", command=self._probe_nodes).pack(side=tk.LEFT, padx=5)
        ttk.Label(btn_frame, text="提示: 双击节点可快速操作", foreground="gray").pack(side=tk.LEFT, padx=20)

    def refresh_nodes(self) -> None:
        for item in self.node_tree.get_children():
            self.node_tree.delete(item)

        nodes = self.get_all_nodes()
        online_nodes = self.get_online_nodes()

        for ip, node in nodes.items():
            status = '在线' if ip in online_nodes else '离线'
            last_heartbeat = datetime.datetime.fromtimestamp(node['last_heartbeat']).strftime("%Y-%m-%d %H:%M:%S")
            self.node_tree.insert('', 'end', values=(ip, node.get('os', 'Unknown'), status, last_heartbeat))

    def _probe_nodes(self) -> None:
        self.services.refresh_all()
        messagebox.showinfo("提示", "节点探测完成")

    def _on_double_click(self, event: tk.Event) -> None:
        selection = self.node_tree.selection()
        if not selection:
            return
        item = self.node_tree.item(selection[0])
        ip = item['values'][0]
        self.services.selected_node_ip.set(ip)

        menu = tk.Menu(self.services.root, tearoff=0)
        menu.add_command(label=f"已选择: {ip}", state='disabled')
        menu.add_separator()
        menu.add_command(label="发送文件", command=lambda: self._quick_action("file_transfer"))
        menu.add_command(label="执行任务", command=lambda: self._quick_action("task"))
        menu.add_command(label="远程命令", command=lambda: self._quick_action("remote_cmd"))
        menu.add_command(label="性能监控", command=lambda: self._quick_action("monitor"))
        menu.post(event.x_root, event.y_root)

    def _show_context_menu(self, event: tk.Event) -> None:
        item = self.node_tree.identify_row(event.y)
        if item:
            self.node_tree.selection_set(item)
            selected_ip = self.node_tree.item(item)['values'][0]
            self.services.selected_node_ip.set(selected_ip)
            self.node_context_menu.post(event.x_root, event.y_root)

    def _quick_action(self, action: str) -> None:
        ip = self.services.selected_node_ip.get()
        if not ip:
            messagebox.showwarning("提示", "请先选择一个节点")
            return
        if self._quick_action_cb:
            self._quick_action_cb(action, ip)
