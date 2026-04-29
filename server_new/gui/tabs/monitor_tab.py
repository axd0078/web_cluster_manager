#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""性能监控标签页。"""

import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import datetime
from typing import Optional
from gui.base_tab import BaseTab, ServiceContainer


class MonitorTab(BaseTab):
    def __init__(self, notebook: ttk.Notebook, title: str, services: ServiceContainer) -> None:
        self.monitor_node_listbox: Optional[tk.Listbox] = None
        self.monitor_text: Optional[scrolledtext.ScrolledText] = None
        self.cpu_threshold_var: Optional[tk.StringVar] = None
        self.memory_threshold_var: Optional[tk.StringVar] = None
        super().__init__(notebook, title, services)

    def _create_widgets(self) -> None:
        left_frame = ttk.Frame(self.frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)

        node_list_frame = ttk.LabelFrame(left_frame, text="在线节点列表")
        node_list_frame.pack(fill=tk.BOTH, expand=True, pady=5)

        listbox_frame = ttk.Frame(node_list_frame)
        listbox_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        scrollbar_list = ttk.Scrollbar(listbox_frame)
        scrollbar_list.pack(side=tk.RIGHT, fill=tk.Y)

        self.monitor_node_listbox = tk.Listbox(listbox_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar_list.set)
        self.monitor_node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_list.config(command=self.monitor_node_listbox.yview)

        ttk.Button(node_list_frame, text="刷新节点列表", command=self.refresh_nodes).pack(pady=5)

        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)

        ttk.Button(btn_frame, text="开始监控选中节点", command=self._start_monitoring).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="停止监控选中节点", command=self._stop_monitoring).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="停止所有监控", command=self._stop_all).pack(side=tk.LEFT, padx=2)

        threshold_frame = ttk.LabelFrame(left_frame, text="告警阈值")
        threshold_frame.pack(fill=tk.X, pady=5)

        ttk.Label(threshold_frame, text="CPU阈值(%):").pack(side=tk.LEFT, padx=5)
        self.cpu_threshold_var = tk.StringVar(value=str(self.services.config['monitoring']['cpu_threshold']))
        ttk.Entry(threshold_frame, textvariable=self.cpu_threshold_var, width=10).pack(side=tk.LEFT, padx=5)

        ttk.Label(threshold_frame, text="内存阈值(%):").pack(side=tk.LEFT, padx=5)
        self.memory_threshold_var = tk.StringVar(value=str(self.services.config['monitoring']['memory_threshold']))
        ttk.Entry(threshold_frame, textvariable=self.memory_threshold_var, width=10).pack(side=tk.LEFT, padx=5)

        right_frame = ttk.Frame(self.frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)

        monitor_frame = ttk.LabelFrame(right_frame, text="监控数据")
        monitor_frame.pack(fill=tk.BOTH, expand=True)

        self.monitor_text = scrolledtext.ScrolledText(monitor_frame, height=15)
        self.monitor_text.pack(fill=tk.BOTH, expand=True)

    def refresh_nodes(self) -> None:
        if not self.monitor_node_listbox:
            return
        self.monitor_node_listbox.delete(0, tk.END)
        online_nodes = self.get_online_nodes()
        for ip, node in self.get_all_nodes().items():
            status = '在线' if ip in online_nodes else '离线'
            display_text = f"{ip} ({node.get('os', 'Unknown')}) - {status}"
            self.monitor_node_listbox.insert(tk.END, display_text)

    def _append_monitor(self, text: str) -> None:
        self.monitor_text.insert(tk.END, text)
        self.monitor_text.see(tk.END)

    def _start_monitoring(self) -> None:
        selected_indices = self.monitor_node_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选择要监控的节点")
            return

        selected_ips = [self.monitor_node_listbox.get(i).split()[0] for i in selected_indices]

        # 绑定监控数据回调
        ms = self.services.monitor_service
        ms.set_data_callback(self._on_monitor_data)
        ms.set_alert_callback(self._on_monitor_alert)

        result = ms.start_monitoring(selected_ips)
        if result['success_count'] > 0:
            msg = f"成功启动 {result['success_count']} 个节点的监控"
            if result['fail_count'] > 0:
                msg += f"，{result['fail_count']} 个节点失败"
            messagebox.showinfo("提示", msg)
        else:
            messagebox.showerror("错误", "所有节点启动监控失败")

    def _stop_monitoring(self) -> None:
        selected_indices = self.monitor_node_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选择要停止监控的节点")
            return
        selected_ips = [self.monitor_node_listbox.get(i).split()[0] for i in selected_indices]
        self.services.monitor_service.stop_monitoring(selected_ips)
        messagebox.showinfo("提示", "已停止选中节点的监控")

    def _stop_all(self) -> None:
        self.services.monitor_service.stop_all()
        messagebox.showinfo("提示", "已停止所有监控")

    def _on_monitor_data(self, target_ip: str, monitor_data: dict) -> None:
        """接收监控数据的回调。"""
        cpu = monitor_data.get('cpu_percent', 0)
        memory = monitor_data.get('memory_percent', 0)
        disk = monitor_data.get('disk_percent', 0)

        info = "═══════════════════════════════════════\n"
        info += f"节点IP: {target_ip}\n"
        info += f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        info += f"CPU: {cpu:.2f}% | 内存: {memory:.2f}% | 磁盘: {disk:.2f}%\n"
        info += f"主机名: {monitor_data.get('hostname', 'N/A')}\n"
        info += f"操作系统: {monitor_data.get('os', 'N/A')}\n"
        if 'memory_total' in monitor_data:
            memory_total_gb = monitor_data.get('memory_total', 0) / (1024**3)
            memory_used_gb = monitor_data.get('memory_used', 0) / (1024**3)
            info += f"内存: {memory_used_gb:.2f}GB / {memory_total_gb:.2f}GB\n"
        info += "═══════════════════════════════════════\n"

        self._append_monitor(info)

        try:
            cpu_threshold = float(self.cpu_threshold_var.get())
            memory_threshold = float(self.memory_threshold_var.get())
            alerts = self.services.monitor_service.check_alerts(
                target_ip, monitor_data, cpu_threshold, memory_threshold)
            if alerts:
                alert_msg = f"告警 - {target_ip}:\n" + "\n".join(alerts)
                self._append_monitor(f"⚠️ {alert_msg}\n\n")
                if not self.services.monitor_service.should_suppress_alert(target_ip):
                    messagebox.showwarning("性能告警", alert_msg)
        except ValueError:
            pass

    def _on_monitor_alert(self, target_ip: str, alert_msg: str) -> None:
        """告警回调。"""
        self._append_monitor(f"⚠️ 告警 - {target_ip}: {alert_msg}\n\n")
