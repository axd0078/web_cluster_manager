#!/usr/bin/env python
# -*- coding: utf-8 -*-

import socket
import threading
import json
import os
import time
import datetime
from pathlib import Path
import zipfile
import io

from .node_manager import NodeManager


class NetworkManager:
    """网络通信管理器"""
    def __init__(self, command_port, monitor_port, node_manager, log_callback):
        self.command_port = command_port
        self.monitor_port = monitor_port
        self.node_manager = node_manager
        self.log_callback = log_callback
        self.command_socket = None
        self.monitor_socket = None
        self.running = False
        self.pending_backups = {}  # 存储待处理的备份文件
        self.backup_lock = threading.Lock()  # 备份文件访问锁
        self.backup_lock = threading.Lock()  # 备份文件访问锁
    
    def start(self):
        self.running = True
        # 启动命令端口监听
        threading.Thread(target=self._listen_commands, daemon=True).start()
        # 启动监控端口监听
        threading.Thread(target=self._listen_monitor, daemon=True).start()
    
    def _listen_commands(self):
        self.command_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.command_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.command_socket.bind(('0.0.0.0', self.command_port))
        self.command_socket.listen(10)
        
        while self.running:
            try:
                conn, addr = self.command_socket.accept()
                threading.Thread(target=self._handle_command, args=(conn, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    self.log_callback(f"命令端口错误: {e}")
    
    def _listen_monitor(self):
        self.monitor_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.monitor_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.monitor_socket.bind(('0.0.0.0', self.monitor_port))
        self.monitor_socket.listen(10)
        self.log_callback(f"监控端口 {self.monitor_port} 已启动监听")
        
        while self.running:
            try:
                conn, addr = self.monitor_socket.accept()
                self.log_callback(f"监控端口收到来自 {addr[0]}:{addr[1]} 的连接")
                threading.Thread(target=self._handle_monitor, args=(conn, addr), daemon=True).start()
            except Exception as e:
                if self.running:
                    self.log_callback(f"监控端口错误: {e}")
    
    def _handle_command(self, conn, addr):
        msg = None
        try:
            # 接收完整消息（可能需要多次接收）
            data = b''
            conn.settimeout(10)
            while True:
                chunk = conn.recv(4096)
                if not chunk:
                    break
                data += chunk
                # 尝试解析JSON，如果成功说明数据接收完整
                try:
                    msg = json.loads(data.decode('utf-8'))
                    break
                except json.JSONDecodeError:
                    # 数据还没接收完整，继续接收
                    continue
            
            if msg:
                if msg.get('type') == 'register':
                    self.node_manager.add_node(addr[0], msg.get('os'), msg.get('info'))
                    conn.send(json.dumps({'status': 'ok'}).encode('utf-8'))
                elif msg.get('type') == 'heartbeat':
                    # 心跳消息中包含os和info信息，用于首次注册
                    self.node_manager.update_heartbeat(
                        addr[0], 
                        msg.get('os'), 
                        msg.get('info')
                    )
                    conn.send(json.dumps({'status': 'ok'}).encode('utf-8'))
                elif msg.get('type') == 'task_result':
                    self.log_callback(f"节点 {addr[0]} 任务执行结果: {msg.get('result')}")
                elif msg.get('type') == 'backup_file':
                    # 接收备份文件
                    self.log_callback(f"收到节点 {addr[0]} 的备份文件请求，大小: {msg.get('file_size', 0)} 字节")
                    threading.Thread(target=self._receive_backup_file, args=(conn, addr, msg), daemon=True).start()
                    return  # 不关闭连接，让线程处理
        except socket.timeout:
            self.log_callback(f"接收命令超时: {addr[0]}")
        except Exception as e:
            self.log_callback(f"处理命令错误: {e}")
            import traceback
            self.log_callback(f"错误详情: {traceback.format_exc()}")
        finally:
            if msg and msg.get('type') != 'backup_file':  # 备份文件由线程处理，不在这里关闭
                conn.close()
            elif not msg:
                conn.close()
    
    def _receive_backup_file(self, conn, addr, msg):
        """接收备份文件"""
        try:
            folder_name = msg.get('folder_name', 'backup')
            file_size = msg.get('file_size', 0)
            
            self.log_callback(f"开始接收节点 {addr[0]} 的备份文件，大小: {file_size} 字节")
            
            # 优化TCP性能：设置接收缓冲区大小和禁用Nagle算法
            conn.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024 * 1024)  # 1MB接收缓冲区
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 禁用Nagle算法
            
            # 发送准备就绪
            conn.send('ready'.encode('utf-8'))
            
            # 接收文件数据
            # 使用更大的缓冲区（128KB）减少系统调用次数，提升接收速度
            BUFFER_SIZE = 131072  # 128KB
            file_data = b''
            received = 0
            conn.settimeout(300)  # 5分钟超时
            last_log_percent = 0
            while received < file_size:
                chunk = conn.recv(min(BUFFER_SIZE, file_size - received))
                if not chunk:
                    break
                file_data += chunk
                received += len(chunk)
                # 减少日志记录频率：每10%记录一次，避免频繁日志影响性能
                current_percent = received * 100 // file_size if file_size > 0 else 0
                if current_percent >= last_log_percent + 10 or received == file_size:
                    self.log_callback(f"已接收 {received}/{file_size} 字节 ({current_percent}%)")
                    last_log_percent = current_percent
            
            if received != file_size:
                self.log_callback(f"警告：节点 {addr[0]} 的备份文件接收不完整，期望: {file_size} 字节，实际: {received} 字节")
            else:
                self.log_callback(f"成功接收节点 {addr[0]} 的备份文件，大小: {file_size} 字节")
            
            # 将备份数据存储，等待GUI处理
            with self.backup_lock:
                self.pending_backups[addr[0]] = {
                    'data': file_data,
                    'folder_name': folder_name,
                    'size': file_size,
                    'received': received
                }
            
            self.log_callback(f"备份文件已存储到pending_backups，IP: {addr[0]}")
            
            # 发送确认
            conn.send(json.dumps({'status': 'success', 'message': '备份文件已接收'}).encode('utf-8'))
        except Exception as e:
            self.log_callback(f"接收备份文件错误: {e}")
            import traceback
            self.log_callback(f"错误详情: {traceback.format_exc()}")
            try:
                conn.send(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
            except:
                pass
        finally:
            conn.close()
    
    def _handle_monitor(self, conn, addr):
        try:
            # 设置接收超时
            conn.settimeout(10)
            # 接收完整数据
            data = b''
            try:
                while True:
                    chunk = conn.recv(4096)
                    if not chunk:
                        break
                    data += chunk
                    # 尝试解析JSON，如果成功说明数据接收完整
                    try:
                        msg = json.loads(data.decode('utf-8'))
                        if msg.get('type') == 'monitor_data':
                            # 更新节点监控信息
                            with self.node_manager.lock:
                                if addr[0] in self.node_manager.nodes:
                                    self.node_manager.nodes[addr[0]]['monitor'] = msg.get('data')
                                    self.log_callback(f"收到节点 {addr[0]} 的监控数据: CPU={msg.get('data', {}).get('cpu_percent', 0):.2f}%")
                                else:
                                    # 如果节点不存在，先添加节点
                                    self.node_manager.add_node(addr[0], msg.get('data', {}).get('os', 'Unknown'), {})
                                    self.node_manager.nodes[addr[0]]['monitor'] = msg.get('data')
                                    self.log_callback(f"收到新节点 {addr[0]} 的监控数据")
                            break
                    except json.JSONDecodeError:
                        # 数据还没接收完整，继续接收
                        continue
            except socket.timeout:
                # 超时，尝试解析已接收的数据
                if data:
                    try:
                        msg = json.loads(data.decode('utf-8'))
                        if msg.get('type') == 'monitor_data':
                            with self.node_manager.lock:
                                if addr[0] in self.node_manager.nodes:
                                    self.node_manager.nodes[addr[0]]['monitor'] = msg.get('data')
                                    self.log_callback(f"收到节点 {addr[0]} 的监控数据（超时后）")
                    except:
                        self.log_callback(f"接收节点 {addr[0]} 监控数据超时或数据不完整")
        except Exception as e:
            self.log_callback(f"处理监控数据错误 {addr[0]}: {e}")
        finally:
            try:
                conn.close()
            except:
                pass
    
    def send_command(self, target_ip, command, params=None):
        """向指定节点发送命令"""
        try:
            # 连接客户端的监听端口（默认8887）
            client_port = 8887
            self.log_callback(f"尝试连接节点 {target_ip}:{client_port} 发送命令: {command}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(10)  # 增加超时时间
            sock.connect((target_ip, client_port))
            self.log_callback(f"已连接到节点 {target_ip}:{client_port}")
            
            msg = {
                'type': 'command',
                'command': command,
                'params': params or {}
            }
            msg_data = json.dumps(msg).encode('utf-8')
            sock.sendall(msg_data)  # 使用sendall确保数据完整发送
            self.log_callback(f"命令已发送到节点 {target_ip}")
            
            # 接收响应
            response_data = b''
            sock.settimeout(30)  # 增加超时时间，因为备份操作可能需要较长时间
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                # 尝试解析JSON，如果成功说明数据接收完整
                try:
                    response = json.loads(response_data.decode('utf-8'))
                    self.log_callback(f"收到节点 {target_ip} 的响应: {response}")
                    sock.close()
                    return response
                except json.JSONDecodeError:
                    continue
            
            sock.close()
            if response_data:
                try:
                    return json.loads(response_data.decode('utf-8'))
                except json.JSONDecodeError as e:
                    self.log_callback(f"解析节点 {target_ip} 响应失败: {e}, 原始数据: {response_data[:200]}")
                    return None
            self.log_callback(f"节点 {target_ip} 未返回响应数据")
            return None
        except socket.timeout:
            self.log_callback(f"连接节点 {target_ip}:8887 超时")
            return None
        except ConnectionRefusedError:
            self.log_callback(f"节点 {target_ip}:8887 拒绝连接，请检查客户端是否运行")
            return None
        except Exception as e:
            self.log_callback(f"发送命令到 {target_ip}:8887 失败: {e}")
            return None
    
    def send_file(self, target_ip, file_path, remote_path):
        """向指定节点发送文件（支持任意类型）"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            # 优化TCP性能：设置发送缓冲区大小和禁用Nagle算法
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)  # 1MB发送缓冲区
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)  # 禁用Nagle算法，减少延迟
            
            # 根据文件大小动态设置超时时间（每MB增加1秒，最少60秒，最多300秒）
            file_size = os.path.getsize(file_path) if Path(file_path).is_file() else 0
            timeout = max(60, min(300, 60 + (file_size // (1024 * 1024))))
            sock.settimeout(timeout)
            
            # 连接客户端的监听端口（默认8887）
            client_port = 8887
            sock.connect((target_ip, client_port))
            
            path = Path(file_path)
            
            if not path.is_file():
                self.log_callback(f"错误：{file_path} 不是文件")
                if sock:
                    sock.close()
                return {'status': 'error', 'message': '选择的路径不是文件'}
            
            # 发送单个文件（支持任意类型）
            msg = {
                'type': 'file_update',
                'update_type': 'single_file',
                'remote_path': remote_path,
                'file_size': file_size,
                'is_zip': False
            }
            sock.sendall(json.dumps(msg).encode('utf-8'))
            
            # 等待确认（设置超时）
            sock.settimeout(10)
            ack = sock.recv(1024).decode('utf-8')
            if ack == 'ready':
                # 发送文件内容（使用sendall确保完整发送）
                # 使用更大的缓冲区（128KB）减少系统调用次数，提升传输速度
                BUFFER_SIZE = 131072  # 128KB
                sent = 0
                last_log_percent = 0
                with open(file_path, 'rb') as f:
                    while sent < file_size:
                        data = f.read(BUFFER_SIZE)
                        if not data:
                            break
                        sock.sendall(data)  # 使用sendall确保完整发送
                        sent += len(data)
                        # 减少日志记录频率：每5%记录一次，避免频繁日志影响性能
                        current_percent = sent * 100 // file_size
                        if current_percent >= last_log_percent + 5 or sent == file_size:
                            self.log_callback(f"已发送 {sent}/{file_size} 字节 ({current_percent}%)")
                            last_log_percent = current_percent
            
            # 接收响应（设置超时）
            sock.settimeout(30)
            response_data = b''
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_data += chunk
                # 尝试解析JSON，如果成功说明数据接收完整
                try:
                    response = json.loads(response_data.decode('utf-8'))
                    if sock:
                        sock.close()
                    return response
                except json.JSONDecodeError:
                    continue
            
            if sock:
                sock.close()
            if response_data:
                return json.loads(response_data.decode('utf-8'))
            return None
        except socket.timeout:
            self.log_callback(f"发送文件到 {target_ip} 超时")
            if sock:
                try:
                    sock.close()
                except:
                    pass
            return {'status': 'error', 'message': '文件传输超时'}
        except Exception as e:
            self.log_callback(f"发送文件到 {target_ip} 失败: {e}")
            import traceback
            self.log_callback(f"错误详情: {traceback.format_exc()}")
            if sock:
                try:
                    sock.close()
                except:
                    pass
            return {'status': 'error', 'message': str(e)}
    
    def stop(self):
        self.running = False
        if self.command_socket:
            self.command_socket.close()
        if self.monitor_socket:
            self.monitor_socket.close()