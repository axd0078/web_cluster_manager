#!/usr/bin/env python
# -*- coding: utf-8 -*-

import base64
import socket
import threading
import traceback
import json
import os
from pathlib import Path
from typing import Any, Callable

from shared.protocol import (
    CLIENT_LISTEN_PORT,
    STREAM_BUFFER_SIZE,
    FILE_BUFFER_SIZE,
    CONNECT_TIMEOUT,
    COMMAND_TIMEOUT,
    FILE_TRANSFER_TIMEOUT,
    MsgType,
    recv_json,
    send_json,
    broadcast,
)
from .node_manager import NodeManager


class NetworkManager:
    """网络通信管理器"""

    def __init__(self, command_port: int, monitor_port: int,
                 node_manager: NodeManager,
                 log_callback: Callable[[str], None]) -> None:
        self.command_port = command_port
        self.monitor_port = monitor_port
        self.node_manager = node_manager
        self.log_callback = log_callback
        self.command_socket = None
        self.monitor_socket = None
        self.running = False
        self.pending_backups = {}  # 存储待处理的备份文件
        self.backup_lock = threading.Lock()  # 备份文件访问锁
    
    def start(self) -> None:
        self.running = True
        # 启动命令端口监听
        threading.Thread(target=self._listen_commands, daemon=True).start()
        # 启动监控端口监听
        threading.Thread(target=self._listen_monitor, daemon=True).start()
    
    def _listen_commands(self) -> None:
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
    
    def _listen_monitor(self) -> None:
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
    
    def _handle_command(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        msg = None
        try:
            msg = recv_json(conn, timeout=CONNECT_TIMEOUT)

            if msg.get('type') == MsgType.REGISTER:
                self.node_manager.add_node(addr[0], msg.get('os'), msg.get('info'))
                send_json(conn, {'status': 'ok'})
            elif msg.get('type') == MsgType.HEARTBEAT:
                self.node_manager.update_heartbeat(
                    addr[0],
                    msg.get('os'),
                    msg.get('info')
                )
                send_json(conn, {'status': 'ok'})
            elif msg.get('type') == MsgType.TASK_RESULT:
                self.log_callback(f"节点 {addr[0]} 任务执行结果: {msg.get('result')}")
            elif msg.get('type') == MsgType.BACKUP_FILE:
                self.log_callback(f"收到节点 {addr[0]} 的备份文件请求，大小: {msg.get('file_size', 0)} 字节")
                threading.Thread(target=self._receive_backup_file, args=(conn, addr, msg), daemon=True).start()
                return  # 不关闭连接，让线程处理
        except (socket.timeout, ConnectionError) as e:
            self.log_callback(f"接收命令失败: {addr[0]} - {e}")
        except Exception as e:
            self.log_callback(f"处理命令错误: {e}")
            self.log_callback(f"错误详情: {traceback.format_exc()}")
        finally:
            if msg and msg.get('type') != MsgType.BACKUP_FILE:
                conn.close()
            elif not msg:
                conn.close()
    
    def _receive_backup_file(self, conn: socket.socket, addr: tuple[str, int],
                              msg: dict[str, Any]) -> None:
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
            file_data = b''
            received = 0
            conn.settimeout(FILE_TRANSFER_TIMEOUT)
            last_log_percent = 0
            while received < file_size:
                chunk = conn.recv(min(FILE_BUFFER_SIZE, file_size - received))
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
            send_json(conn, {'status': 'success', 'message': '备份文件已接收'})
        except Exception as e:
            self.log_callback(f"接收备份文件错误: {e}")
            self.log_callback(f"错误详情: {traceback.format_exc()}")
            try:
                send_json(conn, {'status': 'error', 'message': str(e)})
            except:
                pass
        finally:
            conn.close()
    
    def _handle_monitor(self, conn: socket.socket, addr: tuple[str, int]) -> None:
        try:
            msg = recv_json(conn, timeout=CONNECT_TIMEOUT)
            if msg.get('type') == MsgType.MONITOR_DATA:
                with self.node_manager.lock:
                    if addr[0] in self.node_manager.nodes:
                        self.node_manager.nodes[addr[0]]['monitor'] = msg.get('data')
                        cpu = msg.get('data', {}).get('cpu_percent', 0)
                        self.log_callback(f"收到节点 {addr[0]} 的监控数据: CPU={cpu:.2f}%")
                    else:
                        self.node_manager.add_node(addr[0], msg.get('data', {}).get('os', 'Unknown'), {})
                        self.node_manager.nodes[addr[0]]['monitor'] = msg.get('data')
                        self.log_callback(f"收到新节点 {addr[0]} 的监控数据")
        except (socket.timeout, ConnectionError):
            self.log_callback(f"接收节点 {addr[0]} 监控数据超时或数据不完整")
        except Exception as e:
            self.log_callback(f"处理监控数据错误 {addr[0]}: {e}")
        finally:
            try:
                conn.close()
            except Exception:
                pass
    
    def send_command(self, target_ip: str, command: str,
                      params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """向指定节点发送命令"""
        try:
            self.log_callback(f"尝试连接节点 {target_ip}:{CLIENT_LISTEN_PORT} 发送命令: {command}")
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(CONNECT_TIMEOUT)
            sock.connect((target_ip, CLIENT_LISTEN_PORT))
            self.log_callback(f"已连接到节点 {target_ip}:{CLIENT_LISTEN_PORT}")

            send_json(sock, {
                'type': MsgType.COMMAND,
                'command': command,
                'params': params or {}
            })
            self.log_callback(f"命令已发送到节点 {target_ip}")

            response = recv_json(sock, timeout=COMMAND_TIMEOUT)
            self.log_callback(f"收到节点 {target_ip} 的响应: {response}")
            sock.close()
            return response
        except socket.timeout:
            self.log_callback(f"连接节点 {target_ip}:{CLIENT_LISTEN_PORT} 超时")
            return None
        except ConnectionRefusedError:
            self.log_callback(f"节点 {target_ip}:{CLIENT_LISTEN_PORT} 拒绝连接，请检查客户端是否运行")
            return None
        except ConnectionError:
            self.log_callback(f"节点 {target_ip} 未返回响应数据")
            return None
        except Exception as e:
            self.log_callback(f"发送命令到 {target_ip}:{CLIENT_LISTEN_PORT} 失败: {e}")
            return None
    
    def send_file(self, target_ip: str, file_path: str,
                   remote_path: str) -> dict[str, Any] | None:
        """向指定节点发送文件（支持任意类型）"""
        sock = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1024 * 1024)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

            file_size = os.path.getsize(file_path) if Path(file_path).is_file() else 0
            timeout = max(60, min(FILE_TRANSFER_TIMEOUT, 60 + (file_size // (1024 * 1024))))
            sock.settimeout(timeout)

            sock.connect((target_ip, CLIENT_LISTEN_PORT))

            path = Path(file_path)
            if not path.is_file():
                self.log_callback(f"错误：{file_path} 不是文件")
                sock.close()
                return {'status': 'error', 'message': '选择的路径不是文件'}

            send_json(sock, {
                'type': MsgType.FILE_UPDATE,
                'update_type': 'single_file',
                'remote_path': remote_path,
                'file_size': file_size,
                'is_zip': False
            })

            sock.settimeout(CONNECT_TIMEOUT)
            ack = sock.recv(1024).decode('utf-8')
            if ack == 'ready':
                sent = 0
                last_log_percent = 0
                with open(file_path, 'rb') as f:
                    while sent < file_size:
                        data = f.read(FILE_BUFFER_SIZE)
                        if not data:
                            break
                        sock.sendall(data)
                        sent += len(data)
                        current_percent = sent * 100 // file_size
                        if current_percent >= last_log_percent + 5 or sent == file_size:
                            self.log_callback(f"已发送 {sent}/{file_size} 字节 ({current_percent}%)")
                            last_log_percent = current_percent

            response = recv_json(sock, timeout=COMMAND_TIMEOUT)
            sock.close()
            return response
        except socket.timeout:
            self.log_callback(f"发送文件到 {target_ip} 超时")
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            return {'status': 'error', 'message': '文件传输超时'}
        except Exception as e:
            self.log_callback(f"发送文件到 {target_ip} 失败: {e}")
            self.log_callback(f"错误详情: {traceback.format_exc()}")
            if sock:
                try:
                    sock.close()
                except Exception:
                    pass
            return {'status': 'error', 'message': str(e)}
    
    def send_command_to_multiple(self, target_ips: list[str], command: str,
                                  params: dict[str, Any] | None = None) -> dict[str, Any]:
        """向多个节点发送命令（并发）"""
        return broadcast(
            target_ips,
            lambda ip: self.send_command(ip, command, params),
            timeout=COMMAND_TIMEOUT
        )

    def send_file_to_multiple(self, target_ips: list[str], file_path: str,
                               remote_path: str) -> dict[str, Any]:
        """向多个节点发送文件（并发）"""
        return broadcast(
            target_ips,
            lambda ip: self.send_file(ip, file_path, remote_path),
            timeout=FILE_TRANSFER_TIMEOUT
        )

    def execute_remote_command(self, target_ip: str, cmd: str,
                                timeout: int = 30) -> dict[str, Any] | None:
        """在远程节点执行命令"""
        return self.send_command(target_ip, 'execute_command', {'cmd': cmd, 'timeout': timeout})

    def execute_remote_command_on_multiple(self, target_ips: list[str], cmd: str,
                                            timeout: int = 30) -> dict[str, Any]:
        """在多个远程节点执行命令（并发）"""
        return self.send_command_to_multiple(target_ips, 'execute_command', {'cmd': cmd, 'timeout': timeout})
    
    def get_remote_system_info(self, target_ip: str) -> dict[str, Any] | None:
        """获取远程节点系统信息"""
        return self.send_command(target_ip, 'get_system_info', {})

    # ==================== 更新相关方法 ====================

    def check_client_version(self, target_ip: str) -> dict[str, Any] | None:
        """检查客户端版本"""
        return self.send_command(target_ip, 'get_version', {})

    def get_client_files_manifest(self, target_ip: str) -> dict[str, Any] | None:
        """获取客户端文件清单"""
        return self.send_command(target_ip, 'get_files_manifest', {})

    def push_update_to_client(self, target_ip: str, update_data: bytes | dict[str, Any],
                               new_version: str, update_type: str = 'incremental') -> dict[str, Any]:
        """推送更新到客户端"""
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(FILE_TRANSFER_TIMEOUT)
            self.log_callback(f"尝试连接客户端 {target_ip}:{CLIENT_LISTEN_PORT} 推送更新...")
            sock.connect((target_ip, CLIENT_LISTEN_PORT))

            send_json(sock, {
                'type': MsgType.UPDATE,
                'version': new_version,
                'update_type': update_type
            })
            self.log_callback(f"已发送更新请求到 {target_ip}")

            sock.settimeout(COMMAND_TIMEOUT)
            ack = sock.recv(1024).decode('utf-8')
            if ack != 'ready':
                sock.close()
                return {'status': 'error', 'message': f'客户端未准备就绪: {ack}'}

            self.log_callback(f"客户端 {target_ip} 已准备就绪，开始发送更新数据...")

            if update_type == 'full':
                sock.sendall(update_data)
            else:
                encoded_data = {}
                for file_path, content in update_data.items():
                    if isinstance(content, bytes):
                        encoded_data[file_path] = base64.b64encode(content).decode('utf-8')
                    else:
                        encoded_data[file_path] = content
                sock.sendall(json.dumps(encoded_data).encode('utf-8'))

            self.log_callback(f"更新数据已发送到 {target_ip}，等待响应...")
            sock.shutdown(socket.SHUT_WR)

            response = recv_json(sock, timeout=60)
            sock.close()
            return response
        except socket.timeout:
            return {'status': 'error', 'message': '连接超时'}
        except ConnectionRefusedError:
            return {'status': 'error', 'message': '客户端拒绝连接'}
        except Exception as e:
            return {'status': 'error', 'message': f'推送更新失败: {str(e)}'}

    def push_update_to_multiple(self, target_ips: list[str], update_data: bytes | dict[str, Any],
                                 new_version: str, update_type: str = 'incremental') -> dict[str, Any]:
        """向多个客户端推送更新（并发）"""
        return broadcast(
            target_ips,
            lambda ip: self.push_update_to_client(ip, update_data, new_version, update_type),
            timeout=FILE_TRANSFER_TIMEOUT
        )

    def stop(self) -> None:
        self.running = False
        if self.command_socket:
            self.command_socket.close()
        if self.monitor_socket:
            self.monitor_socket.close()