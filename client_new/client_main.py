#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web集群管理客户端 - 主入口文件
接收服务端指令，执行任务，上报监控信息
"""

import socket
import threading
import json
import os
import time
import platform
import logging
from pathlib import Path

from core.address_pool import AddressPool
from core.task_executor import TaskExecutor
from core.system_monitor import SystemMonitor
from core.client_updater import ClientUpdater


class Client:
    """客户端主类"""
    def __init__(self, config_path):
        # 加载配置
        with open(config_path, 'r', encoding='utf-8') as f:
            self.config = json.load(f)
        
        # 运行标志
        self.running = False
        
        # 日志（先初始化日志，以便后续使用）
        self.log_dir = Path(config_path).parent / 'log'
        self.log_dir.mkdir(exist_ok=True)
        self._setup_logging()
    
    def _setup_logging(self):
        """设置日志配置"""
        # 清除现有的处理器
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)
        
        # 清除所有子logger的处理器
        for logger_name in logging.root.manager.loggerDict:
            logger_obj = logging.getLogger(logger_name)
            for handler in logger_obj.handlers[:]:
                handler.close()
                logger_obj.removeHandler(handler)
        
        # 重置根logger的级别
        root_logger.setLevel(logging.DEBUG)
        
        # 创建新的处理器
        file_handler = logging.FileHandler(self.log_dir / f"{time.strftime('%Y-%m-%d')}.txt", encoding='utf-8')
        stream_handler = logging.StreamHandler()
        
        # 设置格式
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)
        
        # 添加处理器到根logger
        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        
        self.logger = logging.getLogger(__name__)
        
        # 初始化组件
        self.address_pool = AddressPool(self.config['server_addresses'])
        self.task_executor = TaskExecutor(
            self.config['backup_path'],
            self.config['web_app_path'],
            self.logger,  # 传递logger以便清理日志时关闭文件句柄
            self.log_dir  # 传递日志目录
        )
        self.monitor = SystemMonitor()

        # 初始化更新器（使用日志目录的父目录作为客户端目录）
        self.updater = ClientUpdater(self.log_dir.parent)
        
        # 网络配置
        # 兼容旧配置：如果存在command_port，使用它作为client_listen_port
        if 'command_port' in self.config and 'client_listen_port' not in self.config:
            # 旧配置：command_port是客户端监听端口
            self.client_listen_port = self.config['command_port']
            self.server_command_port = self.config.get('monitor_port', 8888)  # 旧配置中monitor_port实际是服务端命令端口
            self.server_monitor_port = 8889  # 默认监控端口
        else:
            # 新配置
            self.client_listen_port = self.config.get('client_listen_port', 8887)
            self.server_command_port = self.config.get('server_command_port', 8888)
            self.server_monitor_port = self.config.get('server_monitor_port', 8889)
        
        self.logger.info(f"客户端配置 - 监听端口: {self.client_listen_port}, 服务端命令端口: {self.server_command_port}, 服务端监控端口: {self.server_monitor_port}")
    
    def start(self):
        """启动客户端"""
        self.running = True
        
        # 启动命令端口监听线程（客户端监听命令端口，接收服务端命令）
        command_thread = threading.Thread(target=self._listen_commands, daemon=True)
        command_thread.start()
        
        # 注意：客户端不需要监听监控端口，监控数据是客户端主动上报给服务端的
        # 监控端口监听线程已移除
        
        # 启动心跳线程
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()
        
        # 立即发送一次注册心跳，不等待
        self._send_heartbeat_immediate()
        
        # 启动监控数据上报线程
        monitor_report_thread = threading.Thread(target=self._monitor_report_loop, daemon=True)
        monitor_report_thread.start()
        
        self.logger.info("客户端已启动")
        
        # 保持主线程运行
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()
    
    def _listen_commands(self):
        """监听命令端口"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('0.0.0.0', self.client_listen_port))
            sock.listen(10)
            
            self.logger.info(f"命令端口监听: {self.client_listen_port}")
            
            while self.running:
                try:
                    conn, addr = sock.accept()
                    self.logger.info(f"收到来自 {addr[0]}:{addr[1]} 的连接")
                    
                    # 检查来源地址
                    if not self.address_pool.is_allowed(addr[0]):
                        self.logger.warning(f"拒绝未授权连接: {addr[0]} (允许的地址: {self.address_pool.allowed_addresses})")
                        conn.close()
                        continue
                    
                    self.logger.info(f"接受来自 {addr[0]} 的连接")
                    threading.Thread(target=self._handle_command, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    if self.running:
                        self.logger.error(f"命令端口接受连接错误: {e}")
        except OSError as e:
            self.logger.error(f"命令端口绑定失败 {self.client_listen_port}: {e}")
            self.logger.error("可能端口已被占用，请检查是否有其他实例在运行")
        finally:
            try:
                sock.close()
            except:
                pass
    
    def _handle_command(self, conn, addr):
        """处理命令"""
        try:
            data = conn.recv(4096).decode('utf-8')
            if not data:
                return
            
            msg = json.loads(data)
            msg_type = msg.get('type')
            
            if msg_type == 'command':
                # 执行命令
                command = msg.get('command')
                params = msg.get('params', {})
                
                result = None
                if command == 'clean_log':
                    # 从参数中获取日期
                    log_date = params.get('date')
                    # 传递回调函数以便在删除当天日志后重新创建日志处理器
                    result = self.task_executor.clean_log(log_date, self._setup_logging)
                    # 发送结果
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"执行命令: {command}, 结果: {result}")
                elif command == 'backup':
                    # 备份整个客户端目录并发送到服务端
                    # 使用连接来源的IP作为服务端IP（因为服务端连接到客户端）
                    server_ip = addr[0]
                    # 立即返回响应，告诉服务端命令已接收
                    result = {'status': 'success', 'message': '备份命令已接收，正在处理...'}
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"执行命令: {command}, 已发送初始响应")
                    # 在后台线程中执行备份和发送文件
                    def backup_async():
                        try:
                            backup_result = self.task_executor.backup_files(server_ip, self.config.get('server_addresses', []), self.server_command_port)
                            self.logger.info(f"备份完成: {backup_result}")
                        except Exception as e:
                            self.logger.error(f"备份过程出错: {e}")
                    threading.Thread(target=backup_async, daemon=True).start()
                elif command == 'start_monitor':
                    self.monitor.start_monitoring()
                    self.logger.info("监控已启动，开始上报数据")
                    result = {'status': 'success', 'message': '监控已启动'}
                    # 发送结果
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"执行命令: {command}, 结果: {result}")
                elif command == 'stop_monitor':
                    self.monitor.stop_monitoring()
                    self.logger.info("监控已停止")
                    result = {'status': 'success', 'message': '监控已停止'}
                    # 发送结果
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"执行命令: {command}, 结果: {result}")
                elif command == 'execute_command':
                    # 执行远程命令
                    cmd = params.get('cmd', '')
                    timeout = params.get('timeout', 30)
                    if not cmd:
                        result = {'status': 'error', 'message': '命令不能为空'}
                    else:
                        self.logger.info(f"执行远程命令: {cmd}")
                        result = self.task_executor.execute_command(cmd, timeout)
                        self.logger.info(f"命令执行结果: {result.get('return_code', -1)}")
                    conn.send(json.dumps(result).encode('utf-8'))
                elif command == 'get_system_info':
                    # 获取系统详细信息
                    result = self.task_executor.get_system_info()
                    result['status'] = 'success'
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"获取系统信息: {result.get('hostname', 'unknown')}")
                elif command == 'get_version':
                    # 获取客户端版本
                    result = {
                        'status': 'success',
                        'version': self.updater.get_local_version()
                    }
                    conn.send(json.dumps(result).encode('utf-8'))
                elif command == 'get_files_manifest':
                    # 获取客户端文件清单
                    manifest = self.updater.get_local_files_manifest()
                    result = {
                        'status': 'success',
                        'manifest': manifest
                    }
                    conn.send(json.dumps(result).encode('utf-8'))
                else:
                    result = {'status': 'error', 'message': f'未知命令: {command}'}
                    # 发送结果
                    conn.send(json.dumps(result).encode('utf-8'))
                    self.logger.info(f"执行命令: {command}, 结果: {result}")
            
            elif msg_type == 'file_update':
                # 文件更新
                remote_path = msg.get('remote_path')
                file_size = msg.get('file_size', 0)
                is_zip = msg.get('is_zip', False)
                update_type = msg.get('update_type', 'single_file')

                # 发送准备就绪
                conn.send('ready'.encode('utf-8'))

                # 接收文件数据
                file_data = b''
                received = 0
                while received < file_size:
                    chunk = conn.recv(min(4096, file_size - received))
                    if not chunk:
                        break
                    file_data += chunk
                    received += len(chunk)

                # 更新文件
                result = self.task_executor.update_file(file_data, remote_path, file_size, is_zip)
                conn.send(json.dumps(result).encode('utf-8'))
                self.logger.info(f"文件更新 ({update_type}): {remote_path}, 结果: {result}")

            elif msg_type == 'update':
                # 客户端更新
                new_version = msg.get('version')
                update_type = msg.get('update_type', 'incremental')

                self.logger.info(f"收到更新请求: 版本 {new_version}, 类型: {update_type}")

                # 发送准备就绪
                conn.send('ready'.encode('utf-8'))

                # 接收更新数据
                update_data = b''
                conn.settimeout(300)  # 5分钟超时

                while True:
                    try:
                        chunk = conn.recv(65536)  # 64KB chunks
                        if not chunk:
                            break
                        update_data += chunk
                        # 对于增量更新，尝试解析JSON判断是否接收完整
                        if update_type == 'incremental':
                            try:
                                json.loads(update_data.decode('utf-8'))
                                break  # JSON解析成功，数据接收完整
                            except:
                                continue
                        else:
                            # 全量更新，等待连接关闭或超时
                            conn.setblocking(False)
                            try:
                                more_data = conn.recv(1)
                                if not more_data:
                                    break
                                update_data += more_data
                            except:
                                break
                            conn.setblocking(True)
                    except socket.timeout:
                        break

                self.logger.info(f"更新数据接收完成: {len(update_data)} 字节")

                # 处理更新数据
                if update_type == 'incremental':
                    # 增量更新：解码JSON并还原bytes
                    import base64
                    try:
                        encoded_data = json.loads(update_data.decode('utf-8'))
                        update_dict = {}
                        for file_path, content in encoded_data.items():
                            try:
                                update_dict[file_path] = base64.b64decode(content)
                            except:
                                update_dict[file_path] = content
                        result = self.updater.apply_update(update_dict, new_version, 'incremental')
                    except Exception as e:
                        result = {'status': 'error', 'message': f'解析更新数据失败: {str(e)}'}
                else:
                    # 全量更新：直接使用bytes
                    result = self.updater.apply_update(update_data, new_version, 'full')

                conn.send(json.dumps(result).encode('utf-8'))
                self.logger.info(f"更新结果: {result}")

                # 如果更新成功，清理旧备份并重启
                if result.get('status') == 'success':
                    self.updater.cleanup_old_backups(keep_count=3)

                    # 延迟重启，给服务端响应时间
                    self._schedule_restart(delay=2)
            
        except Exception as e:
            self.logger.error(f"处理命令错误: {e}")
            try:
                conn.send(json.dumps({'status': 'error', 'message': str(e)}).encode('utf-8'))
            except:
                pass
        finally:
            conn.close()
    
    def _send_heartbeat_immediate(self):
        """立即发送心跳（用于启动时注册）"""
        for server_ip in self.address_pool.allowed_addresses:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(3)
                sock.connect((server_ip, self.server_command_port))
                
                msg = {
                    'type': 'heartbeat',
                    'os': platform.system(),
                    'info': {
                        'hostname': platform.node(),
                        'os_version': platform.version()
                    }
                }
                sock.send(json.dumps(msg).encode('utf-8'))
                response = sock.recv(1024)
                sock.close()
                self.logger.info(f"已向服务端 {server_ip} 注册")
            except Exception as e:
                self.logger.warning(f"注册失败 {server_ip}: {e}")
    
    def _heartbeat_loop(self):
        """心跳循环"""
        time.sleep(2)  # 等待2秒，让立即发送的心跳先完成
        while self.running:
            try:
                # 向所有允许的服务端地址发送心跳
                for server_ip in self.address_pool.allowed_addresses:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(3)
                        sock.connect((server_ip, self.server_command_port))
                        
                        # 发送注册/心跳信息
                        msg = {
                            'type': 'heartbeat',
                            'os': platform.system(),
                            'info': {
                                'hostname': platform.node(),
                                'os_version': platform.version()
                            }
                        }
                        sock.send(json.dumps(msg).encode('utf-8'))
                        
                        response = sock.recv(1024)
                        sock.close()
                    except Exception as e:
                        self.logger.debug(f"心跳发送失败 {server_ip}: {e}")
                
                time.sleep(10)  # 每10秒发送一次心跳
            except Exception as e:
                self.logger.error(f"心跳循环错误: {e}")
                time.sleep(10)
    
    def _monitor_report_loop(self):
        """监控数据上报循环"""
        while self.running:
            try:
                if self.monitor.is_monitoring():
                    # 获取监控数据
                    monitor_data = self.monitor.get_system_info()
                    self.logger.debug(f"准备上报监控数据: CPU={monitor_data.get('cpu_percent', 0):.2f}%, Memory={monitor_data.get('memory_percent', 0):.2f}%")
                    
                    # 向所有允许的服务端地址上报
                    for server_ip in self.address_pool.allowed_addresses:
                        try:
                            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                            sock.settimeout(5)
                            self.logger.debug(f"连接到服务端 {server_ip}:{self.server_monitor_port} 上报监控数据")
                            sock.connect((server_ip, self.server_monitor_port))
                            
                            msg = {
                                'type': 'monitor_data',
                                'data': monitor_data
                            }
                            msg_json = json.dumps(msg)
                            sock.sendall(msg_json.encode('utf-8'))
                            self.logger.debug(f"监控数据已发送到 {server_ip}")
                            # 等待服务端处理（可选，用于确保数据发送完成）
                            sock.shutdown(socket.SHUT_WR)
                            sock.close()
                        except Exception as e:
                            self.logger.warning(f"监控数据上报失败 {server_ip}:{self.server_monitor_port} - {e}")
                else:
                    self.logger.debug("监控未启动，跳过数据上报")
                
                time.sleep(5)  # 每5秒上报一次
            except Exception as e:
                self.logger.error(f"监控上报循环错误: {e}")
                time.sleep(5)

    def _schedule_restart(self, delay=2):
        """
        计划延迟重启客户端

        Args:
            delay: 延迟秒数
        """
        import subprocess
        import sys

        def restart_async():
            time.sleep(delay)

            self.logger.info("正在重启客户端...")

            # 获取当前Python解释器和脚本路径
            python_exe = sys.executable
            script_path = Path(__file__).resolve()

            # 构建重启命令
            if platform.system() == 'Windows':
                # Windows: 使用start命令在新窗口启动
                subprocess.Popen(
                    [python_exe, str(script_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=str(script_path.parent)
                )
            else:
                # Linux/Unix: 使用nohup在后台启动
                subprocess.Popen(
                    ['nohup', python_exe, str(script_path), '&'],
                    cwd=str(script_path.parent)
                )

            # 停止当前客户端
            self.logger.info("客户端即将重启...")
            self.running = False

        # 在后台线程中执行重启
        threading.Thread(target=restart_async, daemon=True).start()

    def stop(self):
        """停止客户端"""
        self.running = False
        self.logger.info("客户端已停止")


def main():
    # 获取配置文件路径（修改为当前目录下的config.json）
    config_path = Path(__file__).parent / 'config.json'
    
    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        print("请确保config.json文件存在于客户端目录中")
        return
    
    client = Client(config_path)
    client.start()


if __name__ == '__main__':
    main()