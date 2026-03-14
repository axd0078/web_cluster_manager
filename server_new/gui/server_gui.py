#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import json
import time
import datetime
import platform
from pathlib import Path

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger
from core.update_manager import UpdateManager


class ServerGUI:
    """服务端GUI界面"""
    def __init__(self, root):
        self.root = root
        self.root.title("Web集群管理服务端")
        self.root.geometry("1200x800")

        # 加载配置（修改路径为当前目录下的config.json）
        config_path = Path(__file__).parent.parent / 'config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)

        # 初始化组件
        self.node_manager = NodeManager()
        self.logger = Logger(Path(__file__).parent.parent / 'logs')
        self.update_manager = UpdateManager()

        # 创建网络管理器
        self.network = NetworkManager(
            self.config['server']['command_port'],
            self.config['server']['monitor_port'],
            self.node_manager,
            self._log_message
        )

        # 存储当前选中的节点IP（用于跨标签页共享）
        self.selected_node_ip = tk.StringVar()

        # 创建界面
        self._create_widgets()

        # 启动网络服务
        self.network.start()

        # 启动定时任务
        self._start_timers()

        # 初始化监控节点列表
        self._refresh_monitor_nodes()
    
    def _create_widgets(self):
        # 创建Notebook（标签页）
        notebook = ttk.Notebook(self.root)
        notebook.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 节点管理标签页
        self._create_node_tab(notebook)

        # 节点分组标签页
        self._create_group_tab(notebook)

        # 任务管理标签页
        self._create_task_tab(notebook)

        # 文件传输标签页
        self._create_update_tab(notebook)

        # 客户端更新标签页
        self._create_client_update_tab(notebook)

        # 批量分发标签页
        self._create_batch_tab(notebook)

        # 远程命令标签页
        self._create_remote_cmd_tab(notebook)

        # 性能监控标签页
        self._create_monitor_tab(notebook)

        # 日志查看标签页
        self._create_log_tab(notebook)
    
    def _create_node_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="节点管理")

        # 节点列表
        tree_frame = ttk.Frame(frame)
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

        # 绑定双击事件
        self.node_tree.bind('<Double-1>', self._on_node_double_click)

        # 创建右键菜单
        self.node_context_menu = tk.Menu(self.root, tearoff=0)
        self.node_context_menu.add_command(label="发送文件", command=lambda: self._quick_action("file_transfer"))
        self.node_context_menu.add_command(label="执行任务", command=lambda: self._quick_action("task"))
        self.node_context_menu.add_command(label="远程命令", command=lambda: self._quick_action("remote_cmd"))
        self.node_context_menu.add_command(label="性能监控", command=lambda: self._quick_action("monitor"))
        self.node_context_menu.add_separator()
        self.node_context_menu.add_command(label="添加到分组", command=lambda: self._quick_action("add_to_group"))
        self.node_tree.bind('<Button-3>', self._show_node_context_menu)

        # 按钮区域
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(btn_frame, text="刷新节点列表", command=self._refresh_nodes).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="探测节点", command=self._probe_nodes).pack(side=tk.LEFT, padx=5)
        ttk.Label(btn_frame, text="提示: 双击节点可快速操作", foreground="gray").pack(side=tk.LEFT, padx=20)
    
    def _create_group_tab(self, notebook):
        """创建节点分组标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="节点分组")
        
        # 左侧：分组列表
        left_frame = ttk.LabelFrame(frame, text="分组列表")
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 分组列表
        list_frame = ttk.Frame(left_frame)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        scrollbar = ttk.Scrollbar(list_frame)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.group_listbox = tk.Listbox(list_frame, yscrollcommand=scrollbar.set)
        self.group_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.config(command=self.group_listbox.yview)
        
        # 分组操作按钮
        group_btn_frame = ttk.Frame(left_frame)
        group_btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(group_btn_frame, text="新分组名:").pack(side=tk.LEFT, padx=5)
        self.new_group_var = tk.StringVar()
        ttk.Entry(group_btn_frame, textvariable=self.new_group_var, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Button(group_btn_frame, text="创建分组", command=self._create_group).pack(side=tk.LEFT, padx=5)
        ttk.Button(group_btn_frame, text="删除分组", command=self._delete_group).pack(side=tk.LEFT, padx=5)
        
        # 右侧：分组节点
        right_frame = ttk.LabelFrame(frame, text="分组节点")
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        # 节点列表
        node_list_frame = ttk.Frame(right_frame)
        node_list_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        scrollbar2 = ttk.Scrollbar(node_list_frame)
        scrollbar2.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.group_node_listbox = tk.Listbox(node_list_frame, yscrollcommand=scrollbar2.set)
        self.group_node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar2.config(command=self.group_node_listbox.yview)
        
        # 节点操作按钮
        node_btn_frame = ttk.Frame(right_frame)
        node_btn_frame.pack(fill=tk.X, pady=5)

        ttk.Label(node_btn_frame, text="节点IP:").pack(side=tk.LEFT, padx=5)
        self.add_node_ip_var = tk.StringVar()
        self.add_node_ip_combo = ttk.Combobox(node_btn_frame, textvariable=self.add_node_ip_var, width=15)
        self.add_node_ip_combo.pack(side=tk.LEFT, padx=5)
        ttk.Button(node_btn_frame, text="添加节点", command=self._add_node_to_group).pack(side=tk.LEFT, padx=5)
        ttk.Button(node_btn_frame, text="移除节点", command=self._remove_node_from_group).pack(side=tk.LEFT, padx=5)
        
        # 绑定分组选择事件
        self.group_listbox.bind('<<ListboxSelect>>', self._on_group_select)
    
    def _create_task_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="任务管理")

        # 节点选择
        select_frame = ttk.LabelFrame(frame, text="选择节点")
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(select_frame, text="目标节点IP:").pack(side=tk.LEFT, padx=5)
        self.task_ip_var = tk.StringVar()
        self.task_ip_combo = ttk.Combobox(select_frame, textvariable=self.task_ip_var, width=18)
        self.task_ip_combo.pack(side=tk.LEFT, padx=5)

        # 任务选择
        task_frame = ttk.LabelFrame(frame, text="选择任务")
        task_frame.pack(fill=tk.X, padx=5, pady=5)

        self.task_type_var = tk.StringVar(value="clean_log")
        ttk.Radiobutton(task_frame, text="清理日志", variable=self.task_type_var, value="clean_log").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(task_frame, text="文件备份", variable=self.task_type_var, value="backup").pack(side=tk.LEFT, padx=10)

        # 参数输入（根据任务类型显示不同内容）
        param_frame = ttk.LabelFrame(frame, text="任务参数")
        param_frame.pack(fill=tk.X, padx=5, pady=5)

        # 清理日志参数
        clean_log_frame = ttk.Frame(param_frame)
        clean_log_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(clean_log_frame, text="日期:").pack(side=tk.LEFT, padx=5)
        self.task_date_var = tk.StringVar(value=datetime.datetime.now().strftime("%Y-%m-%d"))
        ttk.Entry(clean_log_frame, textvariable=self.task_date_var, width=12).pack(side=tk.LEFT, padx=5)
        ttk.Button(clean_log_frame, text="今天", command=lambda: self.task_date_var.set(datetime.datetime.now().strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Button(clean_log_frame, text="昨天", command=lambda: self.task_date_var.set((datetime.datetime.now() - datetime.timedelta(days=1)).strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Button(clean_log_frame, text="前天", command=lambda: self.task_date_var.set((datetime.datetime.now() - datetime.timedelta(days=2)).strftime("%Y-%m-%d"))).pack(side=tk.LEFT, padx=2)
        ttk.Label(clean_log_frame, text="（将删除客户端和服务端指定日期的日志）", foreground="gray").pack(side=tk.LEFT, padx=5)

        # 备份参数
        backup_frame = ttk.Frame(param_frame)
        backup_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Label(backup_frame, text="保存路径:").pack(side=tk.LEFT, padx=5)
        self.task_param_var = tk.StringVar()
        ttk.Entry(backup_frame, textvariable=self.task_param_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(backup_frame, text="选择文件夹", command=self._browse_backup_folder).pack(side=tk.LEFT, padx=5)
        ttk.Label(backup_frame, text="（客户端将压缩整个文件夹并发送到服务端）", foreground="gray").pack(side=tk.LEFT, padx=5)

        # 执行按钮
        ttk.Button(frame, text="执行任务", command=self._execute_task).pack(pady=10)

        # 结果显示
        result_frame = ttk.LabelFrame(frame, text="执行结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.task_result_text = scrolledtext.ScrolledText(result_frame, height=10)
        self.task_result_text.pack(fill=tk.BOTH, expand=True)
    
    def _create_update_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="文件传输")

        # 节点选择
        select_frame = ttk.LabelFrame(frame, text="选择节点")
        select_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(select_frame, text="目标节点IP:").pack(side=tk.LEFT, padx=5)
        self.update_ip_var = tk.StringVar()
        self.update_ip_combo = ttk.Combobox(select_frame, textvariable=self.update_ip_var, width=18)
        self.update_ip_combo.pack(side=tk.LEFT, padx=5)

        # 文件选择
        file_frame = ttk.LabelFrame(frame, text="选择文件（支持任意类型）")
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        self.update_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.update_file_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="浏览", command=self._browse_file).pack(side=tk.LEFT, padx=5)

        # 保存文件名（可选）
        remote_frame = ttk.LabelFrame(frame, text="保存文件名（可选，留空则使用原文件名）")
        remote_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(remote_frame, text="文件名:").pack(side=tk.LEFT, padx=5)
        self.update_remote_var = tk.StringVar()
        ttk.Entry(remote_frame, textvariable=self.update_remote_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Label(remote_frame, text="（文件将保存到客户端的 Transfer Files 文件夹）", foreground="gray").pack(side=tk.LEFT, padx=5)

        # 执行按钮
        ttk.Button(frame, text="开始传输", command=self._start_update).pack(pady=10)

        # 结果显示
        result_frame = ttk.LabelFrame(frame, text="传输结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.update_result_text = scrolledtext.ScrolledText(result_frame, height=10)
        self.update_result_text.pack(fill=tk.BOTH, expand=True)

    def _create_client_update_tab(self, notebook):
        """创建客户端更新标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="客户端更新")

        # 版本信息区域
        version_frame = ttk.LabelFrame(frame, text="版本信息")
        version_frame.pack(fill=tk.X, padx=5, pady=5)

        version_info = self.update_manager.get_version_info()
        ttk.Label(version_frame, text=f"当前版本: {version_info.get('current_version', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Label(version_frame, text=f"发布日期: {version_info.get('release_date', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Label(version_frame, text=f"更新说明: {version_info.get('release_notes', 'N/A')}").pack(side=tk.LEFT, padx=10)
        ttk.Button(version_frame, text="刷新", command=self._refresh_update_version).pack(side=tk.RIGHT, padx=5)

        # 创建更新包区域
        create_frame = ttk.LabelFrame(frame, text="创建更新包")
        create_frame.pack(fill=tk.X, padx=5, pady=5)

        row1 = ttk.Frame(create_frame)
        row1.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(row1, text="客户端源目录:").pack(side=tk.LEFT, padx=5)
        self.update_source_var = tk.StringVar()
        ttk.Entry(row1, textvariable=self.update_source_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(row1, text="浏览", command=self._browse_update_source).pack(side=tk.LEFT, padx=5)

        row2 = ttk.Frame(create_frame)
        row2.pack(fill=tk.X, padx=5, pady=2)
        ttk.Label(row2, text="新版本号:").pack(side=tk.LEFT, padx=5)
        self.new_version_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.new_version_var, width=15).pack(side=tk.LEFT, padx=5)
        ttk.Label(row2, text="更新说明:").pack(side=tk.LEFT, padx=5)
        self.release_notes_var = tk.StringVar()
        ttk.Entry(row2, textvariable=self.release_notes_var, width=40).pack(side=tk.LEFT, padx=5)
        ttk.Button(row2, text="创建更新包", command=self._create_update_package).pack(side=tk.LEFT, padx=10)

        # 目标节点选择区域
        target_frame = ttk.LabelFrame(frame, text="目标节点")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="更新模式:").pack(side=tk.LEFT, padx=5)
        self.client_update_mode_var = tk.StringVar(value="all")
        ttk.Radiobutton(target_frame, text="所有在线节点", variable=self.client_update_mode_var, value="all").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="指定节点", variable=self.client_update_mode_var, value="selected").pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="按分组", variable=self.client_update_mode_var, value="group").pack(side=tk.LEFT, padx=10)

        # 节点选择
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

        # 操作按钮
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        ttk.Button(btn_frame, text="检查客户端版本", command=self._check_client_versions).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="推送更新", command=self._push_update).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="增量更新（智能）", command=self._smart_update).pack(side=tk.LEFT, padx=5)

        # 结果显示
        result_frame = ttk.LabelFrame(frame, text="更新结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.client_update_result_text = scrolledtext.ScrolledText(result_frame, height=12)
        self.client_update_result_text.pack(fill=tk.BOTH, expand=True)

    def _create_batch_tab(self, notebook):
        """创建批量分发标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="批量分发")

        # 目标选择
        target_frame = ttk.LabelFrame(frame, text="目标选择")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="分发模式:").pack(side=tk.LEFT, padx=5)
        self.batch_mode_var = tk.StringVar(value="all")
        ttk.Radiobutton(target_frame, text="所有在线节点", variable=self.batch_mode_var, value="all", command=self._on_batch_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="指定节点", variable=self.batch_mode_var, value="selected", command=self._on_batch_mode_change).pack(side=tk.LEFT, padx=10)
        ttk.Radiobutton(target_frame, text="按分组", variable=self.batch_mode_var, value="group", command=self._on_batch_mode_change).pack(side=tk.LEFT, padx=10)

        # 节点/分组选择区域
        select_frame = ttk.LabelFrame(frame, text="选择节点或分组")
        select_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        # 左侧：节点多选列表
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
        ttk.Button(btn_row, text="全选", command=self._select_all_batch_nodes).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="取消全选", command=lambda: self.batch_node_listbox.selection_clear(0, tk.END)).pack(side=tk.LEFT, padx=2)
        self.batch_selected_label = ttk.Label(btn_row, text="已选: 0 个节点", foreground="blue")
        self.batch_selected_label.pack(side=tk.LEFT, padx=10)
        self.batch_node_listbox.bind('<<ListboxSelect>>', self._update_batch_selected_count)

        # 右侧：分组选择
        right_select_frame = ttk.Frame(select_frame)
        right_select_frame.pack(side=tk.RIGHT, fill=tk.Y, padx=5, pady=5)

        ttk.Label(right_select_frame, text="选择分组:").pack(anchor=tk.W)
        self.batch_group_var = tk.StringVar()
        self.batch_group_combo = ttk.Combobox(right_select_frame, textvariable=self.batch_group_var, width=15, state="readonly")
        self.batch_group_combo.pack(pady=5)

        # 文件选择
        file_frame = ttk.LabelFrame(frame, text="选择文件")
        file_frame.pack(fill=tk.X, padx=5, pady=5)

        self.batch_file_var = tk.StringVar()
        ttk.Entry(file_frame, textvariable=self.batch_file_var, width=50).pack(side=tk.LEFT, padx=5)
        ttk.Button(file_frame, text="浏览", command=self._browse_batch_file).pack(side=tk.LEFT, padx=5)

        # 保存文件名
        remote_frame = ttk.LabelFrame(frame, text="保存文件名")
        remote_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(remote_frame, text="文件名:").pack(side=tk.LEFT, padx=5)
        self.batch_remote_var = tk.StringVar()
        ttk.Entry(remote_frame, textvariable=self.batch_remote_var, width=30).pack(side=tk.LEFT, padx=5)
        ttk.Label(remote_frame, text="（留空则使用原文件名）", foreground="gray").pack(side=tk.LEFT, padx=5)

        # 执行按钮
        ttk.Button(frame, text="开始批量分发", command=self._start_batch_transfer).pack(pady=10)

        # 结果显示
        result_frame = ttk.LabelFrame(frame, text="分发结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.batch_result_text = scrolledtext.ScrolledText(result_frame, height=8)
        self.batch_result_text.pack(fill=tk.BOTH, expand=True)
    
    def _create_remote_cmd_tab(self, notebook):
        """创建远程命令标签页"""
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="远程命令")

        # 目标选择
        target_frame = ttk.LabelFrame(frame, text="目标节点")
        target_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(target_frame, text="节点IP:").pack(side=tk.LEFT, padx=5)
        self.remote_ip_var = tk.StringVar()
        self.remote_ip_combo = ttk.Combobox(target_frame, textvariable=self.remote_ip_var, width=18)
        self.remote_ip_combo.pack(side=tk.LEFT, padx=5)

        ttk.Button(target_frame, text="获取系统信息", command=self._get_remote_info).pack(side=tk.LEFT, padx=10)

        # 命令输入
        cmd_frame = ttk.LabelFrame(frame, text="命令输入")
        cmd_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Label(cmd_frame, text="命令:").pack(side=tk.LEFT, padx=5)
        self.remote_cmd_var = tk.StringVar()
        ttk.Entry(cmd_frame, textvariable=self.remote_cmd_var, width=50).pack(side=tk.LEFT, padx=5)

        ttk.Label(cmd_frame, text="超时(秒):").pack(side=tk.LEFT, padx=5)
        self.remote_timeout_var = tk.StringVar(value="30")
        ttk.Entry(cmd_frame, textvariable=self.remote_timeout_var, width=5).pack(side=tk.LEFT, padx=5)

        ttk.Button(cmd_frame, text="执行", command=self._execute_remote_cmd).pack(side=tk.LEFT, padx=10)

        # 快捷命令
        quick_frame = ttk.LabelFrame(frame, text="快捷命令")
        quick_frame.pack(fill=tk.X, padx=5, pady=5)

        ttk.Button(quick_frame, text="查看IP配置", command=lambda: self._quick_cmd("ipconfig" if platform.system() == "Windows" else "ifconfig")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看磁盘", command=lambda: self._quick_cmd("wmic logicaldisk get size,freespace,caption" if platform.system() == "Windows" else "df -h")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看进程", command=lambda: self._quick_cmd("tasklist" if platform.system() == "Windows" else "ps aux")).pack(side=tk.LEFT, padx=5)
        ttk.Button(quick_frame, text="查看网络连接", command=lambda: self._quick_cmd("netstat -an")).pack(side=tk.LEFT, padx=5)

        # 结果显示
        result_frame = ttk.LabelFrame(frame, text="执行结果")
        result_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)

        self.remote_result_text = scrolledtext.ScrolledText(result_frame, height=15)
        self.remote_result_text.pack(fill=tk.BOTH, expand=True)
    
    def _create_monitor_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="性能监控")
        
        # 左侧：节点选择区域
        left_frame = ttk.Frame(frame)
        left_frame.pack(side=tk.LEFT, fill=tk.BOTH, padx=5, pady=5)
        
        # 节点列表
        node_list_frame = ttk.LabelFrame(left_frame, text="在线节点列表")
        node_list_frame.pack(fill=tk.BOTH, expand=True, pady=5)
        
        # 使用Listbox支持多选
        listbox_frame = ttk.Frame(node_list_frame)
        listbox_frame.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        scrollbar_list = ttk.Scrollbar(listbox_frame)
        scrollbar_list.pack(side=tk.RIGHT, fill=tk.Y)
        
        self.monitor_node_listbox = tk.Listbox(listbox_frame, selectmode=tk.EXTENDED, yscrollcommand=scrollbar_list.set)
        self.monitor_node_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar_list.config(command=self.monitor_node_listbox.yview)
        
        # 刷新节点列表按钮
        ttk.Button(node_list_frame, text="刷新节点列表", command=self._refresh_monitor_nodes).pack(pady=5)
        
        # 操作按钮
        btn_frame = ttk.Frame(left_frame)
        btn_frame.pack(fill=tk.X, pady=5)
        
        ttk.Button(btn_frame, text="开始监控选中节点", command=self._start_monitoring_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="停止监控选中节点", command=self._stop_monitoring_selected).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_frame, text="停止所有监控", command=self._stop_all_monitoring).pack(side=tk.LEFT, padx=2)
        
        # 阈值设置
        threshold_frame = ttk.LabelFrame(left_frame, text="告警阈值")
        threshold_frame.pack(fill=tk.X, pady=5)
        
        ttk.Label(threshold_frame, text="CPU阈值(%):").pack(side=tk.LEFT, padx=5)
        self.cpu_threshold_var = tk.StringVar(value=str(self.config['monitoring']['cpu_threshold']))
        ttk.Entry(threshold_frame, textvariable=self.cpu_threshold_var, width=10).pack(side=tk.LEFT, padx=5)
        
        ttk.Label(threshold_frame, text="内存阈值(%):").pack(side=tk.LEFT, padx=5)
        self.memory_threshold_var = tk.StringVar(value=str(self.config['monitoring']['memory_threshold']))
        ttk.Entry(threshold_frame, textvariable=self.memory_threshold_var, width=10).pack(side=tk.LEFT, padx=5)
        
        # 右侧：监控数据显示
        right_frame = ttk.Frame(frame)
        right_frame.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        monitor_frame = ttk.LabelFrame(right_frame, text="监控数据")
        monitor_frame.pack(fill=tk.BOTH, expand=True)
        
        self.monitor_text = scrolledtext.ScrolledText(monitor_frame, height=15)
        self.monitor_text.pack(fill=tk.BOTH, expand=True)
        
        # 监控状态字典：{ip: True/False}
        self.monitoring_nodes = {}
        self.monitoring_threads = {}  # 存储每个节点的监控线程
        self.last_alert_times = {}  # 存储每个节点的最后告警时间，避免频繁告警
    
    def _create_log_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="操作日志")
        
        # 日志显示
        self.log_text = scrolledtext.ScrolledText(frame, height=30)
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
    
    def _refresh_nodes(self):
        """刷新节点列表"""
        for item in self.node_tree.get_children():
            self.node_tree.delete(item)

        nodes = self.node_manager.get_all_nodes()
        online_nodes = self.node_manager.get_online_nodes()

        for ip, node in nodes.items():
            status = '在线' if ip in online_nodes else '离线'
            last_heartbeat = datetime.datetime.fromtimestamp(node['last_heartbeat']).strftime("%Y-%m-%d %H:%M:%S")
            self.node_tree.insert('', 'end', values=(ip, node.get('os', 'Unknown'), status, last_heartbeat))

        # 同时刷新所有IP下拉框
        self._refresh_ip_comboboxes()

    def _refresh_ip_comboboxes(self):
        """刷新所有IP下拉选择框"""
        online_nodes = self.node_manager.get_online_nodes()
        all_nodes = list(self.node_manager.get_all_nodes().keys())

        # 更新任务管理页的IP下拉框
        if hasattr(self, 'task_ip_combo'):
            self.task_ip_combo['values'] = online_nodes

        # 更新文件传输页的IP下拉框
        if hasattr(self, 'update_ip_combo'):
            self.update_ip_combo['values'] = online_nodes

        # 更新远程命令页的IP下拉框
        if hasattr(self, 'remote_ip_combo'):
            self.remote_ip_combo['values'] = online_nodes

        # 更新客户端更新页的IP下拉框
        if hasattr(self, 'client_update_ip_combo'):
            self.client_update_ip_combo['values'] = online_nodes

        # 更新分组管理页的节点下拉框
        if hasattr(self, 'add_node_ip_combo'):
            self.add_node_ip_combo['values'] = all_nodes

        # 更新批量分发页的节点列表
        if hasattr(self, 'batch_node_listbox'):
            self.batch_node_listbox.delete(0, tk.END)
            for ip in all_nodes:
                status = '在线' if ip in online_nodes else '离线'
                self.batch_node_listbox.insert(tk.END, f"{ip} ({status})")

    def _on_node_double_click(self, event):
        """节点双击事件处理"""
        selection = self.node_tree.selection()
        if not selection:
            return

        item = self.node_tree.item(selection[0])
        ip = item['values'][0]

        # 设置选中的节点IP
        self.selected_node_ip.set(ip)

        # 显示快捷操作菜单
        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label=f"已选择: {ip}", state='disabled')
        menu.add_separator()
        menu.add_command(label="发送文件", command=lambda: self._quick_action("file_transfer"))
        menu.add_command(label="执行任务", command=lambda: self._quick_action("task"))
        menu.add_command(label="远程命令", command=lambda: self._quick_action("remote_cmd"))
        menu.add_command(label="性能监控", command=lambda: self._quick_action("monitor"))
        menu.post(event.x_root, event.y_root)

    def _show_node_context_menu(self, event):
        """显示节点右键菜单"""
        # 选中点击的行
        item = self.node_tree.identify_row(event.y)
        if item:
            self.node_tree.selection_set(item)
            selected_ip = self.node_tree.item(item)['values'][0]
            self.selected_node_ip.set(selected_ip)
            self.node_context_menu.post(event.x_root, event.y_root)

    def _quick_action(self, action):
        """快捷操作"""
        ip = self.selected_node_ip.get()
        if not ip:
            messagebox.showwarning("提示", "请先选择一个节点")
            return

        if action == "file_transfer":
            self.update_ip_var.set(ip)
            # 切换到文件传输标签页
            self.root.winfo_children()[0].select(3)  # 文件传输是第4个标签
        elif action == "task":
            self.task_ip_var.set(ip)
            # 切换到任务管理标签页
            self.root.winfo_children()[0].select(2)  # 任务管理是第3个标签
        elif action == "remote_cmd":
            self.remote_ip_var.set(ip)
            # 切换到远程命令标签页
            self.root.winfo_children()[0].select(5)  # 远程命令是第6个标签
        elif action == "monitor":
            # 切换到性能监控标签页
            self.root.winfo_children()[0].select(6)  # 性能监控是第7个标签
            # 在监控节点列表中选中该节点
            for i in range(self.monitor_node_listbox.size()):
                if self.monitor_node_listbox.get(i).startswith(ip):
                    self.monitor_node_listbox.selection_set(i)
                    break
        elif action == "add_to_group":
            # 切换到节点分组标签页
            self.root.winfo_children()[0].select(1)  # 节点分组是第2个标签
            self.add_node_ip_var.set(ip)
    
    def _probe_nodes(self):
        """探测节点存活"""
        self._refresh_nodes()
        messagebox.showinfo("提示", "节点探测完成")
    
    def _execute_task(self):
        """执行任务"""
        target_ip = self.task_ip_var.get()
        if not target_ip:
            messagebox.showerror("错误", "请输入目标节点IP")
            return
        
        task_type = self.task_type_var.get()
        
        if task_type == 'clean_log':
            # 清理日志任务
            log_date = self.task_date_var.get().strip()
            if not log_date:
                messagebox.showerror("错误", "请输入日期 (格式: YYYY-MM-DD)")
                return
            
            # 验证日期格式
            try:
                datetime.datetime.strptime(log_date, '%Y-%m-%d')
            except ValueError:
                messagebox.showerror("错误", "日期格式错误，请使用 YYYY-MM-DD 格式")
                return
            
            # 先清理服务端日志（指定IP和日期）
            server_result = self.logger.clean_log(log_date, target_ip)
            self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 服务端: {server_result.get('message', '')}\n")
            
            # 再清理客户端日志（指定日期）
            client_result = self.network.send_command(target_ip, task_type, {'date': log_date})
            
            # 记录清理日志操作到主日志文件（logs/YYYY-MM-DD.txt）
            self.logger.log(target_ip, 'clean_log', f"日期: {log_date}")
            
            # 记录清理日志操作到操作日志文件（logs/IP地址/operation_YYYY-MM-DD.txt）
            self.logger.log_operation('清理日志', target_ip, f"日期: {log_date}, 服务端结果: {server_result.get('message', '')}, 客户端结果: {client_result.get('message', '') if client_result else '失败'}")
            
            if client_result:
                self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 客户端 {target_ip}: {client_result.get('message', '')}\n")
            else:
                self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 客户端 {target_ip}: 执行失败\n")
        else:
            # 文件备份任务
            if task_type == 'backup':
                save_path = self.task_param_var.get().strip()
                if not save_path:
                    messagebox.showerror("错误", "请选择保存路径")
                    return
                
                # 记录日志
                self.logger.log(target_ip, task_type, save_path)
                
                # 发送备份命令
                result = self.network.send_command(target_ip, task_type, {})
                
                if result and result.get('status') == 'success':
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 备份命令已发送，等待接收文件...\n")
                    self.task_result_text.see(tk.END)
                    self.root.update()  # 更新GUI，显示消息
                    
                    # 使用after方法在后台等待，避免阻塞GUI
                    self._wait_for_backup_file(target_ip, save_path, 0, 60)  # 增加等待时间到60秒
                else:
                    error_msg = result.get('message', '未知错误') if result else '未收到响应'
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 执行失败 - {error_msg} (响应: {result})\n")
                    self.task_result_text.see(tk.END)
    
    def _wait_for_backup_file(self, target_ip, save_path, waited, max_wait):
        """等待接收备份文件（使用after方法，不阻塞GUI）"""
        if waited >= max_wait:
            self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 等待备份文件超时（{max_wait}秒）\n")
            self.task_result_text.see(tk.END)
            return
        
        # 检查是否有备份文件
        with self.network.backup_lock:
            if target_ip in self.network.pending_backups:
                backup_info = self.network.pending_backups[target_ip]
                
                # 保存备份文件
                try:
                    # 处理路径，支持Windows路径格式
                    save_dir = Path(save_path)
                    # 确保目录存在
                    try:
                        save_dir.mkdir(parents=True, exist_ok=True)
                    except Exception as e:
                        self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 创建保存目录失败: {e}\n")
                        self.task_result_text.see(tk.END)
                        # 清理临时数据
                        if target_ip in self.network.pending_backups:
                            del self.network.pending_backups[target_ip]
                        return
                    
                    # 保存zip文件
                    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
                    zip_filename = f"{backup_info['folder_name']}_backup_{timestamp}.zip"
                    zip_path = save_dir / zip_filename
                    
                    # 如果文件已存在，添加序号
                    counter = 1
                    while zip_path.exists():
                        zip_filename = f"{backup_info['folder_name']}_backup_{timestamp}_{counter}.zip"
                        zip_path = save_dir / zip_filename
                        counter += 1
                    
                    # 写入文件
                    try:
                        with open(zip_path, 'wb') as f:
                            f.write(backup_info['data'])
                    except PermissionError as e:
                        self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 保存备份文件失败（权限不足）: {e}\n")
                        self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 请检查路径是否有写入权限: {zip_path}\n")
                        self.task_result_text.see(tk.END)
                        # 清理临时数据
                        if target_ip in self.network.pending_backups:
                            del self.network.pending_backups[target_ip]
                        return
                    except Exception as e:
                        self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 保存备份文件失败: {e}\n")
                        self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 保存路径: {zip_path}\n")
                        self.task_result_text.see(tk.END)
                        # 清理临时数据
                        if target_ip in self.network.pending_backups:
                            del self.network.pending_backups[target_ip]
                        return
                    
                    # 记录操作日志
                    self.logger.log_operation('文件备份', target_ip, f"保存路径: {zip_path}, 大小: {backup_info['size']} 字节")
                    
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 备份文件已保存到 {zip_path}\n")
                    self.task_result_text.see(tk.END)
                    
                    # 清理临时数据
                    del self.network.pending_backups[target_ip]
                    return
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 保存备份文件失败: {e}\n")
                    self.task_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 错误详情: {error_detail}\n")
                    self.task_result_text.see(tk.END)
                    # 清理临时数据
                    if target_ip in self.network.pending_backups:
                        del self.network.pending_backups[target_ip]
                    return
        
        # 继续等待，1秒后再次检查
        self.root.after(1000, lambda: self._wait_for_backup_file(target_ip, save_path, waited + 1, max_wait))
    
    def _browse_file(self):
        """浏览文件（支持任意类型）"""
        path = filedialog.askopenfilename(title="选择要传输的文件")
        
        if path:
            self.update_file_var.set(path)
            # 如果保存文件名为空，自动填充原文件名
            if not self.update_remote_var.get():
                self.update_remote_var.set(Path(path).name)
    
    def _browse_backup_folder(self):
        """浏览备份保存文件夹"""
        path = filedialog.askdirectory(title="选择备份保存文件夹")
        
        if path:
            self.task_param_var.set(path)
    
    def _start_update(self):
        """开始传输文件"""
        target_ip = self.update_ip_var.get()
        if not target_ip:
            messagebox.showerror("错误", "请输入目标节点IP")
            return
        
        file_path = self.update_file_var.get()
        if not file_path:
            messagebox.showerror("错误", "请选择要传输的文件")
            return
        
        # 检查文件是否存在
        if not Path(file_path).exists():
            messagebox.showerror("错误", "选择的文件不存在")
            return
        
        # 如果未指定保存文件名，使用原文件名
        remote_path = self.update_remote_var.get().strip()
        if not remote_path:
            remote_path = Path(file_path).name
        
        # 记录日志
        self.logger.log(target_ip, "file_transfer", file_path)
        # 记录操作日志
        self.logger.log_operation('文件传输', target_ip, f"文件: {file_path}, 保存为: {remote_path}")
        
        # 在后台线程中发送文件，避免阻塞GUI
        def send_file_async():
            self.update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 开始传输文件...\n")
            self.update_result_text.see(tk.END)
            self.root.update()
            
            result = self.network.send_file(target_ip, file_path, remote_path)
            
            if result:
                if isinstance(result, dict):
                    message = result.get('message', str(result))
                    status = result.get('status', 'unknown')
                    if status == 'success':
                        self.update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 文件传输成功 - {message}\n")
                    else:
                        self.update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 文件传输失败 - {message}\n")
                else:
                    self.update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: {result}\n")
            else:
                self.update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {target_ip}: 文件传输失败 - 未收到响应\n")
            
            self.update_result_text.see(tk.END)
        
        # 在新线程中执行文件传输
        import threading
        threading.Thread(target=send_file_async, daemon=True).start()

    # ==================== 客户端更新方法 ====================

    def _refresh_update_version(self):
        """刷新版本信息"""
        version_info = self.update_manager.get_version_info()
        self.client_update_result_text.insert(tk.END, f"当前版本: {version_info.get('current_version', 'N/A')}\n")
        self.client_update_result_text.insert(tk.END, f"发布日期: {version_info.get('release_date', 'N/A')}\n")
        self.client_update_result_text.insert(tk.END, f"更新说明: {version_info.get('release_notes', 'N/A')}\n")
        self.client_update_result_text.insert(tk.END, f"文件数量: {len(version_info.get('files', {}))}\n")
        self.client_update_result_text.see(tk.END)

    def _browse_update_source(self):
        """浏览客户端源目录"""
        path = filedialog.askdirectory(title="选择客户端源目录")
        if path:
            self.update_source_var.set(path)

    def _create_update_package(self):
        """创建更新包"""
        source_dir = self.update_source_var.get()
        if not source_dir:
            messagebox.showerror("错误", "请选择客户端源目录")
            return

        new_version = self.new_version_var.get().strip()
        if not new_version:
            messagebox.showerror("错误", "请输入新版本号")
            return

        release_notes = self.release_notes_var.get().strip()

        self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 正在创建更新包 v{new_version}...\n")
        self.client_update_result_text.see(tk.END)

        def create_async():
            result = self.update_manager.create_update_package(source_dir, new_version, release_notes)
            if result['status'] == 'success':
                self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {result['message']}\n")
                self.client_update_result_text.insert(tk.END, f"  文件数量: {result.get('files_count', 0)}\n")
                self.client_update_result_text.insert(tk.END, f"  ZIP包: {result.get('package_path', 'N/A')}\n")
                if result.get('exe_path'):
                    self.client_update_result_text.insert(tk.END, f"  EXE文件: {result.get('exe_path')}\n")
            else:
                self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 错误: {result['message']}\n")
            self.client_update_result_text.see(tk.END)

        threading.Thread(target=create_async, daemon=True).start()

    def _check_client_versions(self):
        """检查所有客户端版本"""
        online_nodes = self.node_manager.get_online_nodes()
        if not online_nodes:
            messagebox.showinfo("提示", "没有在线节点")
            return

        self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 正在检查客户端版本...\n")
        self.client_update_result_text.see(tk.END)

        def check_async():
            current_version = self.update_manager.get_current_version()
            self.client_update_result_text.insert(tk.END, f"服务端当前版本: {current_version}\n")
            self.client_update_result_text.insert(tk.END, "-" * 50 + "\n")

            for ip in online_nodes:
                result = self.network.check_client_version(ip)
                if result and result.get('status') == 'success':
                    client_version = result.get('version', 'unknown')
                    status = "最新" if client_version == current_version else "需要更新"
                    self.client_update_result_text.insert(tk.END, f"  {ip}: v{client_version} [{status}]\n")
                else:
                    self.client_update_result_text.insert(tk.END, f"  {ip}: 无法获取版本\n")
            self.client_update_result_text.see(tk.END)

        threading.Thread(target=check_async, daemon=True).start()

    def _push_update(self):
        """推送更新到客户端"""
        # 获取目标节点
        mode = self.client_update_mode_var.get()
        target_ips = []

        if mode == "all":
            target_ips = self.node_manager.get_online_nodes()
        elif mode == "selected":
            ip = self.client_update_ip_var.get().strip()
            if not ip:
                messagebox.showerror("错误", "请选择节点IP")
                return
            target_ips = [ip]
        elif mode == "group":
            group_name = self.client_update_group_var.get().strip()
            if not group_name:
                messagebox.showerror("错误", "请选择分组")
                return
            target_ips = self.node_manager.get_group_nodes(group_name)

        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        # 获取更新包
        update_data = self.update_manager.get_update_package()
        if not update_data:
            messagebox.showerror("错误", "更新包不存在，请先创建更新包")
            return

        new_version = self.update_manager.get_current_version()

        self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 开始推送更新 v{new_version} 到 {len(target_ips)} 个节点...\n")
        self.client_update_result_text.see(tk.END)

        def push_async():
            results = self.network.push_update_to_multiple(target_ips, update_data, new_version, 'full')

            success_count = 0
            fail_count = 0
            for ip, result in results.items():
                if result and result.get('status') == 'success':
                    success_count += 1
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 更新成功\n")
                else:
                    fail_count += 1
                    msg = result.get('message', '未知错误') if result else '无响应'
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 更新失败 - {msg}\n")
                self.client_update_result_text.see(tk.END)

            self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 推送完成: 成功 {success_count}, 失败 {fail_count}\n")
            self.client_update_result_text.see(tk.END)

        threading.Thread(target=push_async, daemon=True).start()

    def _smart_update(self):
        """智能增量更新"""
        # 获取目标节点
        mode = self.client_update_mode_var.get()
        target_ips = []

        if mode == "all":
            target_ips = self.node_manager.get_online_nodes()
        elif mode == "selected":
            ip = self.client_update_ip_var.get().strip()
            if not ip:
                messagebox.showerror("错误", "请选择节点IP")
                return
            target_ips = [ip]
        elif mode == "group":
            group_name = self.client_update_group_var.get().strip()
            if not group_name:
                messagebox.showerror("错误", "请选择分组")
                return
            target_ips = self.node_manager.get_group_nodes(group_name)

        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        new_version = self.update_manager.get_current_version()

        self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 开始智能增量更新...\n")
        self.client_update_result_text.see(tk.END)

        def smart_update_async():
            for ip in target_ips:
                # 获取客户端文件清单
                self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 获取文件清单...\n")
                manifest_result = self.network.get_client_files_manifest(ip)

                if not manifest_result or manifest_result.get('status') != 'success':
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 无法获取文件清单，跳过\n")
                    continue

                client_manifest = manifest_result.get('manifest', {})

                # 获取更新清单
                update_manifest = self.update_manager.get_update_manifest(None, client_manifest)

                if not update_manifest.get('need_update'):
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 已是最新版本，跳过\n")
                    continue

                files_to_update = update_manifest.get('files_to_update', [])
                files_to_delete = update_manifest.get('files_to_delete', [])

                self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 需要更新 {len(files_to_update)} 个文件，删除 {len(files_to_delete)} 个文件\n")

                # 准备增量更新数据
                import base64
                update_data = {}
                for file_path in files_to_update:
                    content = self.update_manager.get_file_content(file_path)
                    if content:
                        update_data[file_path] = content

                if not update_data:
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 没有需要更新的文件\n")
                    continue

                # 推送增量更新
                result = self.network.push_update_to_client(ip, update_data, new_version, 'incremental')

                if result and result.get('status') == 'success':
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 增量更新成功\n")
                else:
                    msg = result.get('message', '未知错误') if result else '无响应'
                    self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 增量更新失败 - {msg}\n")

                self.client_update_result_text.see(tk.END)

            self.client_update_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 智能增量更新完成\n")
            self.client_update_result_text.see(tk.END)

        threading.Thread(target=smart_update_async, daemon=True).start()

    def _refresh_monitor_nodes(self):
        """刷新监控节点列表"""
        self.monitor_node_listbox.delete(0, tk.END)
        nodes = self.node_manager.get_all_nodes()
        online_nodes = self.node_manager.get_online_nodes()
        
        for ip, node in nodes.items():
            status = '在线' if ip in online_nodes else '离线'
            display_text = f"{ip} ({node.get('os', 'Unknown')}) - {status}"
            self.monitor_node_listbox.insert(tk.END, display_text)
    
    def _start_monitoring_selected(self):
        """开始监控选中的节点"""
        selected_indices = self.monitor_node_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选择要监控的节点")
            return
        
        selected_ips = []
        for index in selected_indices:
            text = self.monitor_node_listbox.get(index)
            # 从显示文本中提取IP地址（格式：IP (OS) - 状态）
            ip = text.split()[0]
            selected_ips.append(ip)
        
        success_count = 0
        fail_count = 0
        
        for target_ip in selected_ips:
            if target_ip in self.monitoring_nodes and self.monitoring_nodes[target_ip]:
                self._log_message(f"节点 {target_ip} 已在监控中，跳过")
                continue
            
            # 发送监控指令
            self._log_message(f"向节点 {target_ip} 发送启动监控命令...")
            result = self.network.send_command(target_ip, 'start_monitor', {})
            if result:
                self._log_message(f"节点 {target_ip} 响应: {result}")
                if result.get('status') == 'success':
                    self.monitoring_nodes[target_ip] = True
                    # 启动监控数据收集线程
                    thread = threading.Thread(target=self._collect_monitor_data, args=(target_ip,), daemon=True)
                    thread.start()
                    self.monitoring_threads[target_ip] = thread
                    success_count += 1
                    # 记录操作日志
                    self.logger.log_operation('启动监控', target_ip, '监控已启动')
                else:
                    self._log_message(f"节点 {target_ip} 启动监控失败: {result.get('message', '未知错误')}")
                    fail_count += 1
            else:
                self._log_message(f"向节点 {target_ip} 发送命令失败，请检查节点是否在线")
                fail_count += 1
        
        if success_count > 0:
            messagebox.showinfo("提示", f"成功启动 {success_count} 个节点的监控" + (f"，{fail_count} 个节点失败" if fail_count > 0 else ""))
        else:
            messagebox.showerror("错误", f"所有节点启动监控失败")
    
    def _stop_monitoring_selected(self):
        """停止监控选中的节点"""
        selected_indices = self.monitor_node_listbox.curselection()
        if not selected_indices:
            messagebox.showwarning("提示", "请先选择要停止监控的节点")
            return
        
        selected_ips = []
        for index in selected_indices:
            text = self.monitor_node_listbox.get(index)
            ip = text.split()[0]
            selected_ips.append(ip)
        
        for target_ip in selected_ips:
            if target_ip in self.monitoring_nodes and self.monitoring_nodes[target_ip]:
                self.monitoring_nodes[target_ip] = False
                result = self.network.send_command(target_ip, 'stop_monitor', {})
                if result:
                    self._log_message(f"节点 {target_ip} 停止监控响应: {result}")
                    # 记录操作日志
                    self.logger.log_operation('停止监控', target_ip, '监控已停止')
                # 清理告警时间记录
                if target_ip in self.last_alert_times:
                    del self.last_alert_times[target_ip]
        
        messagebox.showinfo("提示", "已停止选中节点的监控")
    
    def _stop_all_monitoring(self):
        """停止所有监控"""
        if not self.monitoring_nodes:
            messagebox.showinfo("提示", "当前没有正在监控的节点")
            return
        
        for target_ip in list(self.monitoring_nodes.keys()):
            if self.monitoring_nodes[target_ip]:
                self.monitoring_nodes[target_ip] = False
                result = self.network.send_command(target_ip, 'stop_monitor', {})
                if result:
                    self._log_message(f"节点 {target_ip} 停止监控响应: {result}")
                    # 记录操作日志
                    self.logger.log_operation('停止监控', target_ip, '监控已停止')
        
        self.monitoring_nodes.clear()
        self.monitoring_threads.clear()
        self.last_alert_times.clear()
        messagebox.showinfo("提示", "已停止所有监控")
    
    def _collect_monitor_data(self, target_ip):
        """收集监控数据"""
        # 首次显示提示信息
        self.monitor_text.insert(tk.END, f"[{datetime.datetime.now()}] 开始监控节点 {target_ip}，等待数据...\n")
        self.monitor_text.see(tk.END)
        
        while self.monitoring_nodes.get(target_ip, False):
            try:
                # 从节点管理器获取监控数据
                nodes = self.node_manager.get_all_nodes()
                if target_ip in nodes:
                    if 'monitor' in nodes[target_ip] and nodes[target_ip]['monitor']:
                        monitor_data = nodes[target_ip]['monitor']
                        
                        # 显示监控数据
                        cpu = monitor_data.get('cpu_percent', 0)
                        memory = monitor_data.get('memory_percent', 0)
                        disk = monitor_data.get('disk_percent', 0)
                        
                        info = f"═══════════════════════════════════════\n"
                        info += f"节点IP: {target_ip}\n"
                        info += f"时间: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        info += f"CPU: {cpu:.2f}% | 内存: {memory:.2f}% | 磁盘: {disk:.2f}%\n"
                        info += f"主机名: {monitor_data.get('hostname', 'N/A')}\n"
                        info += f"操作系统: {monitor_data.get('os', 'N/A')}\n"
                        if 'memory_total' in monitor_data:
                            memory_total_gb = monitor_data.get('memory_total', 0) / (1024**3)
                            memory_used_gb = monitor_data.get('memory_used', 0) / (1024**3)
                            info += f"内存: {memory_used_gb:.2f}GB / {memory_total_gb:.2f}GB\n"
                        info += f"═══════════════════════════════════════\n"
                        
                        self.monitor_text.insert(tk.END, info)
                        self.monitor_text.see(tk.END)
                        
                        # 检查告警阈值
                        try:
                            cpu_threshold = float(self.cpu_threshold_var.get())
                            memory_threshold = float(self.memory_threshold_var.get())
                            
                            alerts = []
                            if cpu > cpu_threshold:
                                alerts.append(f"CPU使用率过高: {cpu:.2f}% (阈值: {cpu_threshold}%)")
                            if memory > memory_threshold:
                                alerts.append(f"内存使用率过高: {memory:.2f}% (阈值: {memory_threshold}%)")
                            
                            if alerts:
                                alert_msg = f"告警 - {target_ip}:\n" + "\n".join(alerts)
                                self.monitor_text.insert(tk.END, f"⚠️ {alert_msg}\n\n")
                                self.monitor_text.see(tk.END)
                                # 避免频繁弹出告警窗口（每个节点独立计时）
                                current_time = time.time()
                                if target_ip not in self.last_alert_times or current_time - self.last_alert_times[target_ip] > 60:
                                    messagebox.showwarning("性能告警", alert_msg)
                                    self.last_alert_times[target_ip] = current_time
                        except ValueError:
                            pass  # 阈值输入无效，忽略
                    else:
                        # 节点存在但没有监控数据
                        self.monitor_text.insert(tk.END, f"[{datetime.datetime.now()}] 等待节点 {target_ip} 上报监控数据...\n")
                        self.monitor_text.see(tk.END)
                else:
                    # 节点不存在
                    self.monitor_text.insert(tk.END, f"[{datetime.datetime.now()}] 节点 {target_ip} 不存在，请先确认节点已注册\n")
                    self.monitor_text.see(tk.END)
                
                time.sleep(5)  # 每5秒收集一次
            except Exception as e:
                self._log_message(f"收集监控数据错误: {e}")
                self.monitor_text.insert(tk.END, f"[{datetime.datetime.now()}] 错误: {e}\n")
                self.monitor_text.see(tk.END)
                time.sleep(5)
    
    def _log_message(self, message):
        """记录消息到日志标签页"""
        self.log_text.insert(tk.END, f"[{datetime.datetime.now()}] {message}\n")
        self.log_text.see(tk.END)
    
    def _start_timers(self):
        """启动定时任务"""
        def refresh():
            self._refresh_nodes()
            # 如果监控标签页存在，也刷新监控节点列表
            if hasattr(self, 'monitor_node_listbox'):
                self._refresh_monitor_nodes()
            # 刷新分组列表
            if hasattr(self, 'group_listbox'):
                self._refresh_groups()
            self.root.after(5000, refresh)
        
        self.root.after(5000, refresh)
    
    # ==================== 节点分组管理方法 ====================
    
    def _refresh_groups(self):
        """刷新分组列表"""
        if not hasattr(self, 'group_listbox'):
            return

        self.group_listbox.delete(0, tk.END)
        groups = self.node_manager.get_all_groups()

        for group_name in groups:
            self.group_listbox.insert(tk.END, group_name)

        # 更新批量分发的分组下拉框
        if hasattr(self, 'batch_group_combo'):
            self.batch_group_combo['values'] = list(groups.keys())

        # 更新客户端更新的分组下拉框
        if hasattr(self, 'client_update_group_combo'):
            self.client_update_group_combo['values'] = list(groups.keys())
    
    def _on_group_select(self, event):
        """分组选择事件"""
        selection = self.group_listbox.curselection()
        if not selection:
            return
        
        group_name = self.group_listbox.get(selection[0])
        nodes = self.node_manager.get_group_nodes(group_name)
        
        # 更新分组节点列表
        self.group_node_listbox.delete(0, tk.END)
        for ip in nodes:
            node_info = self.node_manager.get_all_nodes().get(ip, {})
            status = '在线' if ip in self.node_manager.get_online_nodes() else '离线'
            self.group_node_listbox.insert(tk.END, f"{ip} ({status})")
    
    def _create_group(self):
        """创建新分组"""
        group_name = self.new_group_var.get().strip()
        if not group_name:
            messagebox.showerror("错误", "请输入分组名称")
            return
        
        if self.node_manager.create_group(group_name):
            self._refresh_groups()
            self.new_group_var.set("")
            messagebox.showinfo("成功", f"分组 '{group_name}' 创建成功")
        else:
            messagebox.showerror("错误", f"分组 '{group_name}' 已存在")
    
    def _delete_group(self):
        """删除分组"""
        selection = self.group_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请选择要删除的分组")
            return
        
        group_name = self.group_listbox.get(selection[0])
        if messagebox.askyesno("确认", f"确定要删除分组 '{group_name}' 吗？"):
            if self.node_manager.delete_group(group_name):
                self._refresh_groups()
                self.group_node_listbox.delete(0, tk.END)
                messagebox.showinfo("成功", f"分组 '{group_name}' 已删除")
    
    def _add_node_to_group(self):
        """添加节点到分组"""
        selection = self.group_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请先选择分组")
            return
        
        group_name = self.group_listbox.get(selection[0])
        ip = self.add_node_ip_var.get().strip()
        
        if not ip:
            messagebox.showerror("错误", "请输入节点IP")
            return
        
        if self.node_manager.add_node_to_group(ip, group_name):
            self._on_group_select(None)
            self.add_node_ip_var.set("")
            messagebox.showinfo("成功", f"节点 {ip} 已添加到分组 '{group_name}'")
        else:
            messagebox.showerror("错误", "添加失败")
    
    def _remove_node_from_group(self):
        """从分组移除节点"""
        selection = self.group_node_listbox.curselection()
        if not selection:
            messagebox.showerror("错误", "请选择要移除的节点")
            return
        
        node_text = self.group_node_listbox.get(selection[0])
        ip = node_text.split()[0]
        
        if self.node_manager.remove_node_from_group(ip):
            self._on_group_select(None)
            messagebox.showinfo("成功", f"节点 {ip} 已从分组移除")
    
    # ==================== 批量分发方法 ====================

    def _on_batch_mode_change(self):
        """批量分发模式改变时更新界面"""
        mode = self.batch_mode_var.get()
        # 根据模式启用/禁用不同的选择控件
        if mode == "all":
            self.batch_node_listbox.config(state='disabled')
            self.batch_group_combo.config(state='disabled')
        elif mode == "selected":
            self.batch_node_listbox.config(state='normal')
            self.batch_group_combo.config(state='disabled')
        elif mode == "group":
            self.batch_node_listbox.config(state='disabled')
            self.batch_group_combo.config(state='readonly')

    def _select_all_batch_nodes(self):
        """全选批量分发节点"""
        self.batch_node_listbox.selection_set(0, tk.END)
        self._update_batch_selected_count()

    def _update_batch_selected_count(self, event=None):
        """更新已选节点数量显示"""
        selected = self.batch_node_listbox.curselection()
        count = len(selected)
        self.batch_selected_label.config(text=f"已选: {count} 个节点")

    def _browse_batch_file(self):
        """浏览批量分发文件"""
        path = filedialog.askopenfilename(title="选择要分发的文件")
        if path:
            self.batch_file_var.set(path)
            if not self.batch_remote_var.get():
                self.batch_remote_var.set(Path(path).name)

    def _start_batch_transfer(self):
        """开始批量分发"""
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

        # 获取目标节点列表
        target_ips = []
        mode = self.batch_mode_var.get()

        if mode == "all":
            target_ips = self.node_manager.get_online_nodes()
        elif mode == "selected":
            # 从listbox获取选中的节点
            selected_indices = self.batch_node_listbox.curselection()
            if not selected_indices:
                messagebox.showerror("错误", "请选择目标节点")
                return
            for index in selected_indices:
                text = self.batch_node_listbox.get(index)
                # 提取IP（格式：IP (状态)）
                ip = text.split()[0]
                target_ips.append(ip)
        elif mode == "group":
            group_name = self.batch_group_var.get().strip()
            if not group_name:
                messagebox.showerror("错误", "请选择分组")
                return
            target_ips = self.node_manager.get_group_nodes(group_name)

        if not target_ips:
            messagebox.showerror("错误", "没有可用的目标节点")
            return

        self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 开始批量分发到 {len(target_ips)} 个节点...\n")
        self.batch_result_text.see(tk.END)

        # 在后台线程中执行批量分发
        def batch_transfer_async():
            results = self.network.send_file_to_multiple(target_ips, file_path, remote_path)

            success_count = 0
            fail_count = 0

            for ip, result in results.items():
                if result and result.get('status') == 'success':
                    success_count += 1
                    self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 成功 - {result.get('message', '')}\n")
                else:
                    fail_count += 1
                    self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] {ip}: 失败 - {result.get('message', '未知错误') if result else '无响应'}\n")
                self.batch_result_text.see(tk.END)

            self.batch_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 批量分发完成: 成功 {success_count}, 失败 {fail_count}\n")
            self.batch_result_text.see(tk.END)

            # 记录操作日志
            self.logger.log_operation('批量文件分发', '多个节点', f"文件: {file_path}, 成功: {success_count}, 失败: {fail_count}")

        threading.Thread(target=batch_transfer_async, daemon=True).start()
    
    # ==================== 远程命令方法 ====================
    
    def _get_remote_info(self):
        """获取远程节点系统信息"""
        ip = self.remote_ip_var.get().strip()
        if not ip:
            messagebox.showerror("错误", "请输入节点IP")
            return
        
        self.remote_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 获取节点 {ip} 的系统信息...\n")
        self.remote_result_text.see(tk.END)
        
        def get_info_async():
            result = self.network.get_remote_system_info(ip)
            
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
                info += f"═══════════════════════════════════════\n"
                
                self.remote_result_text.insert(tk.END, info)
            else:
                self.remote_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 获取系统信息失败: {result.get('message', '无响应') if result else '无响应'}\n")
            
            self.remote_result_text.see(tk.END)
        
        threading.Thread(target=get_info_async, daemon=True).start()
    
    def _execute_remote_cmd(self):
        """执行远程命令"""
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
        
        self.remote_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 在节点 {ip} 执行命令: {cmd}\n")
        self.remote_result_text.see(tk.END)
        
        def execute_async():
            result = self.network.execute_remote_command(ip, cmd, timeout)
            
            if result:
                self.remote_result_text.insert(tk.END, f"返回码: {result.get('return_code', 'N/A')}\n")
                self.remote_result_text.insert(tk.END, f"状态: {result.get('status', 'N/A')}\n")
                
                if result.get('stdout'):
                    self.remote_result_text.insert(tk.END, f"输出:\n{result['stdout']}\n")
                if result.get('stderr'):
                    self.remote_result_text.insert(tk.END, f"错误:\n{result['stderr']}\n")
                
                # 记录操作日志
                self.logger.log_operation('远程命令执行', ip, f"命令: {cmd}, 返回码: {result.get('return_code', 'N/A')}")
            else:
                self.remote_result_text.insert(tk.END, f"[{datetime.datetime.now()}] 命令执行失败: 无响应\n")
            
            self.remote_result_text.see(tk.END)
        
        threading.Thread(target=execute_async, daemon=True).start()
    
    def _quick_cmd(self, cmd):
        """快捷命令"""
        self.remote_cmd_var.set(cmd)
        self._execute_remote_cmd()