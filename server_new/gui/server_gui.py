#!/usr/bin/env python
# -*- coding: utf-8 -*-

import tkinter as tk
from tkinter import ttk, messagebox, filedialog, scrolledtext
import threading
import json
import time
import datetime
from pathlib import Path

from core.node_manager import NodeManager
from core.network_manager import NetworkManager
from core.logger import Logger


class ServerGUI:
    """服务端GUI界面"""
    def __init__(self, root):
        self.root = root
        self.root.title("Web集群管理服务端 12303070227李彦逹")
        self.root.geometry("1200x800")
        
        # 加载配置（修改路径为当前目录下的config.json）
        config_path = Path(__file__).parent.parent / 'config.json'
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # 初始化组件
        self.node_manager = NodeManager()
        self.logger = Logger(Path(__file__).parent.parent / 'logs')
        
        # 创建网络管理器
        self.network = NetworkManager(
            self.config['server']['command_port'],
            self.config['server']['monitor_port'],
            self.node_manager,
            self._log_message
        )
        
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
        
        # 任务管理标签页
        self._create_task_tab(notebook)
        
        # 程序更新标签页
        self._create_update_tab(notebook)
        
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
        
        # 按钮区域
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Button(btn_frame, text="刷新节点列表", command=self._refresh_nodes).pack(side=tk.LEFT, padx=5)
        ttk.Button(btn_frame, text="探测节点", command=self._probe_nodes).pack(side=tk.LEFT, padx=5)
    
    def _create_task_tab(self, notebook):
        frame = ttk.Frame(notebook)
        notebook.add(frame, text="任务管理")
        
        # 节点选择
        select_frame = ttk.LabelFrame(frame, text="选择节点")
        select_frame.pack(fill=tk.X, padx=5, pady=5)
        
        ttk.Label(select_frame, text="目标节点IP:").pack(side=tk.LEFT, padx=5)
        self.task_ip_var = tk.StringVar()
        ttk.Entry(select_frame, textvariable=self.task_ip_var, width=20).pack(side=tk.LEFT, padx=5)
        
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
        ttk.Label(clean_log_frame, text="日期 (YYYY-MM-DD):").pack(side=tk.LEFT, padx=5)
        self.task_date_var = tk.StringVar()
        ttk.Entry(clean_log_frame, textvariable=self.task_date_var, width=20).pack(side=tk.LEFT, padx=5)
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
        ttk.Entry(select_frame, textvariable=self.update_ip_var, width=20).pack(side=tk.LEFT, padx=5)
        
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
            self.root.after(5000, refresh)
        
        self.root.after(5000, refresh)