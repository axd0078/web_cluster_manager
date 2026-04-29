#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点分组标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class GroupTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.group_listbox: Optional[tk.Listbox] = None
        self.group_node_listbox: Optional[tk.Listbox] = None
        self.new_group_var: Optional[tk.StringVar] = None
        self.add_node_ip_var: Optional[tk.StringVar] = None
        self.add_node_ip_combo: Optional[ttk.Combobox] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        left_frame = ttk.LabelFrame(self.frame, text="分组列表")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.group_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        self.group_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.group_listbox.yview)

        group_btn_frame = ttk.Frame(left_frame)
        group_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Label(group_btn_frame, text="新分组名:").pack(side=tk.LEFT, padx=5)
        self.new_group_var = tk.StringVar()
        ttk.Entry(group_btn_frame, textvariable=self.new_group_var, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(group_btn_frame, text="创建分组", command=self._create_group).pack(side=tk.LEFT, padx=5)
        ttk.Button(group_btn_frame, text="删除分组", command=self._delete_group).pack(side=tk.LEFT, padx=5)

        right_frame = ttk.LabelFrame(self.frame, text="分组节点")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        node_list_frame = ttk.Frame(right_frame)
        node_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar2 = ttk.Scrollbar(node_list_frame)
        scrollbar2.pack(side=tk.RIGHT, fill=tk.Y)

        self.group_node_listbox = tk.Listbox(node_list_frame, yscrollcommand=scrollbar2.set)
        self.group_node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar2.config(command=self.group_node_listbox.yview)

        node_btn_frame = ttk.Frame(right_frame)
        node_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Label(node_btn_frame, text="节点IP:").pack(side=tk.LEFT, padx=5)
        self.add_node_ip_var = tk.StringVar()
        self.add_node_ip_combo = ttk.Combobox(node_btn_frame, textvariable=self.add_node_ip_var, width=15)
        self.add_node_ip_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(node_btn_frame, text="添加节点", command=self._add_node_to_group).pack(side=tk.LEFT, padx=5)
        ttk.Button(node_btn_frame, text="移除节点", command=self._remove_node_from_group).pack(side=tk.LEFT, padx=5)

        self.group_listbox.bind('<<ListboxSelect>>', self._on_group_select)

    def refresh_groups(self) -> None:
        if not self.group_listbox:
            return
        self.group_listbox.delete(0, tk.END)
        groups = self.services.node_manager.get_all_groups()
        for group_name in groups:
            self.group_listbox.insert(tk.END, group_name)

    def _on_group_select(self, event: tk.Event | None) -> None:
        selection = self.group_listbox.curselection()
        if not selection:
            return
        group_name = self.group_listbox.get(selection[0])
        nodes = self.services.node_manager.get_group_nodes(group_name)

        self.group_node_listbox.delete(0, tk.END)
        for ip in nodes:
            node_info = self.get_all_nodes().get(ip, {})
            status = '在线' if ip in self.get_online_nodes() else '离线'
            self.group_node_listbox.insert(tk.END, f"{ip} ({status})")

    def _create_group(self) -> None:
        group_name = self.new_group_var.get().strip()
        if not group_name:
            messagebox.showerror("错误", "请输入分组名称")
            return
        if self.services.node_manager.create_group(group_name):
            self.refresh_groups()
            self.new_group_var.set("")
            messagebox.showinfo("成功", f"分组 '{group_name}' 创建成功")
        else:
            messagebox.showerror("错误", f"分组 '{group_name}' 已存在")

    def _delete_group(self) -> None:
        selection = self.group_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请选择要删除的分组")
            return
        group_name = self.group_listbox.get(selection[0])
        if messagebox.askyesno("确认", f"确定要删除分组 '{group_name}' 吗？"):
            if self.services.node_manager.delete_group(group_name):
                self.refresh_groups()
                self.group_node_listbox.delete(0, tk.END)
                messagebox.showinfo("成功", f"分组 '{group_name}' 已删除")

    def _add_node_to_group(self) -> None:
        selection = self.group_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请先选择分组")
            return
        group_name = self.group_listbox.get(selection[0])
        ip = self.add_node_ip_var.get().strip()
        if not ip:
            messagebox.showerror("错误", "请输入节点IP")
            return
        if self.services.node_manager.add_node_to_group(ip, group_name):
            self._on_group_select(None)
            self.add_node_ip_var.set("")
            messagebox.showinfo("成功", f"节点 {ip} 已添加到分组 '{group_name}'")
        else:
            messagebox.showerror("错误", "添加失败")

    def _remove_node_from_group(self) -> None:
        selection = self.group_node_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请选择要移除的节点")
            return
        node_text = self.group_node_listbox.get(selection[0])
        ip = node_text.split()[0]
        if self.services.node_manager.remove_node_from_group(ip):
            self._on_group_select(None)
            messagebox.showinfo("成功", f"节点 {ip} 已从分组移除")
