#!/usr/bin/env python
# -*- coding: utf-8 -*-

import socket
import threading
import json
import os
import time
import logging
import zipfile
import io
import subprocess
import traceback
import platform
import psutil
from pathlib import Path

from shared.protocol import (
    STREAM_BUFFER_SIZE,
    FILE_BUFFER_SIZE,
    CONNECT_TIMEOUT,
    COMMAND_TIMEOUT,
    FILE_TRANSFER_TIMEOUT,
    MsgType,
    send_json,
    recv_json,
)


class TaskExecutor:
    """任务执行器"""
    def __init__(self, backup_path, web_app_path, logger=None, log_dir=None):
        self.backup_path = Path(backup_path)
        self.web_app_path = Path(web_app_path)
        self.backup_path.mkdir(parents=True, exist_ok=True)
        self.web_app_path.mkdir(parents=True, exist_ok=True)
        self.logger = logger
        # 如果提供了log_dir参数，使用它；否则使用模块所在目录的父目录下的log
        if log_dir:
            self.log_dir = Path(log_dir)
        else:
            self.log_dir = Path(__file__).parent.parent / 'log'
    
    def _close_log_file_handlers(self, log_file_path):
        """关闭指定日志文件的所有文件句柄"""
        if not self.logger:
            return
        
        # 获取根logger和所有子logger
        root_logger = logging.getLogger()
        loggers = [root_logger] + [logging.getLogger(name) for name in logging.root.manager.loggerDict]
        
        for logger_obj in loggers:
            handlers_to_remove = []
            for handler in logger_obj.handlers[:]:
                if isinstance(handler, logging.FileHandler):
                    try:
                        handler_path = Path(handler.baseFilename)
                        if handler_path.resolve() == Path(log_file_path).resolve():
                            handler.close()
                            handlers_to_remove.append(handler)
                    except:
                        pass
            
            # 移除已关闭的处理器
            for handler in handlers_to_remove:
                logger_obj.removeHandler(handler)
    
    def clean_log(self, log_date=None, recreate_handler_callback=None):
        """清理日志 - 根据日期删除指定日期的日志文件"""
        try:
            # 使用初始化时设置的日志目录
            log_dir = self.log_dir
            
            if not log_dir.exists():
                return {'status': 'error', 'message': f'日志路径不存在: {log_dir}'}
            
            deleted_files = []
            today = time.strftime('%Y-%m-%d')
            is_today_log = (log_date == today)
            
            if log_date:
                # 根据日期删除日志文件
                # 删除格式为 YYYY-MM-DD.txt 的日志文件
                date_log_file = log_dir / f"{log_date}.txt"
                if date_log_file.exists():
                    # 先关闭文件句柄
                    self._close_log_file_handlers(str(date_log_file))
                    # 等待一下，确保文件句柄完全释放
                    time.sleep(0.2)
                    # 尝试删除，如果失败则重试
                    max_retries = 5
                    deleted = False
                    for i in range(max_retries):
                        try:
                            date_log_file.unlink()
                            deleted_files.append(str(date_log_file))
                            deleted = True
                            break
                        except PermissionError as e:
                            if i < max_retries - 1:
                                time.sleep(0.3)
                            else:
                                # 最后一次尝试失败，返回错误
                                return {'status': 'error', 'message': f'清理日志失败: 文件被占用，无法删除 {date_log_file}。请关闭可能正在使用该文件的程序（如日志查看器、文本编辑器等）。'}
                    
                    # 如果删除的是当天的日志文件，需要重新创建日志处理器
                    if deleted and is_today_log and recreate_handler_callback:
                        recreate_handler_callback()
                
                # 删除格式为 operation_YYYY-MM-DD.txt 的操作日志文件
                operation_log_file = log_dir / f"operation_{log_date}.txt"
                if operation_log_file.exists():
                    try:
                        operation_log_file.unlink()
                        deleted_files.append(str(operation_log_file))
                    except PermissionError:
                        # 如果操作日志文件也被占用，记录但不影响主流程
                        pass
                
                if deleted_files:
                    return {'status': 'success', 'message': f'日志文件已删除: {", ".join(deleted_files)}'}
                else:
                    return {'status': 'error', 'message': f'指定日期的日志文件不存在: {log_date}'}
            else:
                # 如果没有指定日期，返回错误
                return {'status': 'error', 'message': '请指定要清理的日志日期'}
        except Exception as e:
            return {'status': 'error', 'message': f'清理日志失败: {str(e)}'}
    
    def backup_files(self, server_ip, server_addresses, server_command_port):
        """备份文件 - 压缩整个客户端目录并发送到服务端"""
        try:
            # 获取客户端目录（备份目录的父目录）
            client_dir = self.backup_path.parent
            
            # 压缩整个客户端目录到内存
            zip_buffer = io.BytesIO()
            folder_name = client_dir.name  # 获取文件夹名称（如 client_new）
            
            with zipfile.ZipFile(zip_buffer, 'w', zipfile.ZIP_DEFLATED) as zipf:
                # 遍历客户端目录下的所有文件
                for root, dirs, files in os.walk(client_dir):
                    # 排除备份目录、Transfer Files目录、__pycache__目录和日志目录
                    dirs[:] = [d for d in dirs if d not in ['backup', 'Transfer Files', '__pycache__', 'log']]
                    
                    for file in files:
                        file_path = Path(root) / file
                        # 计算相对路径（相对于客户端目录）
                        arcname = file_path.relative_to(client_dir)
                        zipf.write(file_path, arcname)
            
            zip_data = zip_buffer.getvalue()
            zip_size = len(zip_data)
            
            # 发送备份文件到服务端
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(FILE_TRANSFER_TIMEOUT)

                # 连接到服务端命令端口
                sock.connect((server_ip, server_command_port))

                # 发送备份文件请求
                send_json(sock, {
                    'type': MsgType.BACKUP_FILE,
                    'folder_name': folder_name,
                    'file_size': zip_size
                })

                # 等待服务端准备就绪
                sock.settimeout(CONNECT_TIMEOUT)
                ack = sock.recv(1024).decode('utf-8')
                if ack != 'ready':
                    sock.close()
                    return {'status': 'error', 'message': f'服务端未准备就绪: {ack}'}

                # 发送压缩文件数据
                sock.settimeout(FILE_TRANSFER_TIMEOUT)
                sent = 0
                while sent < zip_size:
                    chunk = zip_data[sent:sent + FILE_BUFFER_SIZE]
                    sock.sendall(chunk)
                    sent += len(chunk)

                # 接收响应
                response = recv_json(sock, timeout=COMMAND_TIMEOUT)
                sock.close()
                if response.get('status') == 'success':
                    return {'status': 'success', 'message': '备份文件已发送到服务端'}
                else:
                    return {'status': 'error', 'message': f"服务端接收失败: {response.get('message', '未知错误')}"}
                
            except socket.timeout:
                return {'status': 'error', 'message': '连接服务端超时'}
            except ConnectionRefusedError:
                return {'status': 'error', 'message': '服务端拒绝连接'}
            except Exception as e:
                return {'status': 'error', 'message': f'发送备份文件失败: {str(e)}'}
                    
        except Exception as e:
            error_detail = traceback.format_exc()
            return {'status': 'error', 'message': f'备份失败: {str(e)}'}
    
    def update_file(self, file_data, remote_path, file_size, is_zip=False):
        """更新文件 - 保存到Transfer Files文件夹"""
        try:
            # 创建Transfer Files文件夹（备份目录的父目录下的Transfer Files文件夹）
            transfer_dir = self.backup_path.parent / 'Transfer Files'
            transfer_dir.mkdir(parents=True, exist_ok=True)
            
            if is_zip:
                # 解压zip文件
                zip_buffer = io.BytesIO(file_data)
                with zipfile.ZipFile(zip_buffer, 'r') as zipf:
                    # 如果指定了remote_path，使用它作为解压目录名，否则使用默认名称
                    if remote_path:
                        target_path = transfer_dir / remote_path
                    else:
                        target_path = transfer_dir / 'extracted'
                    target_path.mkdir(parents=True, exist_ok=True)
                    zipf.extractall(target_path)
                
                return {'status': 'success', 'message': f'文件夹解压完成: {target_path}'}
            else:
                # 保存单个文件
                # 如果指定了remote_path，使用它作为文件名，否则从remote_path提取文件名
                if remote_path:
                    # 如果remote_path包含路径分隔符，只取文件名部分
                    filename = Path(remote_path).name if remote_path else 'received_file'
                    target_path = transfer_dir / filename
                else:
                    # 如果没有指定路径，使用默认文件名
                    target_path = transfer_dir / 'received_file'
                
                with open(target_path, 'wb') as f:
                    f.write(file_data)
                
                return {'status': 'success', 'message': f'文件保存完成: {target_path}'}
        except Exception as e:
            return {'status': 'error', 'message': f'文件保存失败: {str(e)}'}
    
    def execute_command(self, command, timeout=COMMAND_TIMEOUT):
        """执行远程命令"""
        try:
            # 安全检查：禁止危险命令
            dangerous_commands = ['rm -rf', 'del /', 'format', 'mkfs', 'dd if=', 
                                  '> /dev/', 'chmod 777', 'chown root']
            for dangerous in dangerous_commands:
                if dangerous in command:
                    return {
                        'status': 'error', 
                        'message': f'禁止执行危险命令: {dangerous}',
                        'return_code': -1
                    }
            
            # 根据操作系统选择shell和编码
            if platform.system() == 'Windows':
                # Windows使用cmd，编码使用系统默认（GBK/cp936）
                encoding = 'gbk'
                process = subprocess.Popen(
                    ['cmd', '/c', command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding=encoding,
                    errors='replace'
                )
            else:
                # Linux/Unix使用bash，编码使用UTF-8
                encoding = 'utf-8'
                process = subprocess.Popen(
                    ['bash', '-c', command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    stdin=subprocess.PIPE,
                    text=True,
                    encoding=encoding,
                    errors='replace'
                )
            
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                return_code = process.returncode
                
                # 限制输出长度
                max_output_length = 10000
                if len(stdout) > max_output_length:
                    stdout = stdout[:max_output_length] + '\n... (输出被截断)'
                if len(stderr) > max_output_length:
                    stderr = stderr[:max_output_length] + '\n... (错误输出被截断)'
                
                return {
                    'status': 'success' if return_code == 0 else 'error',
                    'message': '命令执行完成',
                    'stdout': stdout,
                    'stderr': stderr,
                    'return_code': return_code,
                    'command': command
                }
            except subprocess.TimeoutExpired:
                process.kill()
                return {
                    'status': 'error',
                    'message': f'命令执行超时（超过{timeout}秒）',
                    'return_code': -1,
                    'command': command
                }
        except Exception as e:
            return {
                'status': 'error',
                'message': f'命令执行失败: {str(e)}',
                'return_code': -1,
                'command': command
            }
    
    def get_system_info(self):
        """获取系统详细信息"""
        try:
            cpu_count = psutil.cpu_count(logical=True)
            cpu_count_physical = psutil.cpu_count(logical=False)
            memory = psutil.virtual_memory()
            
            # 获取磁盘信息
            disks = []
            if platform.system() == 'Windows':
                for partition in psutil.disk_partitions():
                    try:
                        usage = psutil.disk_usage(partition.mountpoint)
                        disks.append({
                            'mountpoint': partition.mountpoint,
                            'total': usage.total,
                            'used': usage.used,
                            'percent': usage.percent
                        })
                    except:
                        pass
            else:
                usage = psutil.disk_usage('/')
                disks.append({
                    'mountpoint': '/',
                    'total': usage.total,
                    'used': usage.used,
                    'percent': usage.percent
                })
            
            return {
                'hostname': platform.node(),
                'os': platform.system(),
                'os_version': platform.version(),
                'cpu_count_logical': cpu_count,
                'cpu_count_physical': cpu_count_physical,
                'memory_total': memory.total,
                'memory_available': memory.available,
                'disks': disks,
                'python_version': platform.python_version()
            }
        except Exception as e:
            return {'error': str(e)}