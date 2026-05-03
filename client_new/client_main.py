#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web集群管理客户端 - 主入口文件
接收服务端指令，执行任务，上报监控信息
"""

import base64
import socket
import subprocess
import sys
import threading
import json
import time
import platform
import logging
from pathlib import Path
from typing import Any

from core.address_pool import AddressPool
from core.task_executor import TaskExecutor
from core.system_monitor import SystemMonitor
from core.client_updater import ClientUpdater
from shared.protocol import (
    CLIENT_LISTEN_PORT,
    SERVER_COMMAND_PORT,
    SERVER_MONITOR_PORT,
    STREAM_BUFFER_SIZE,
    LARGE_BUFFER_SIZE,
    CONNECT_TIMEOUT,
    COMMAND_TIMEOUT,
    FILE_TRANSFER_TIMEOUT,
    HEARTBEAT_INTERVAL,
    MONITOR_INTERVAL,
    REGISTER_TIMEOUT,
    MsgType,
    send_json,
    recv_json,
)


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

        # 初始化组件
        self.address_pool = AddressPool(self.config['server_addresses'])
        self.task_executor = TaskExecutor(
            self.config['backup_path'],
            self.config['web_app_path'],
            self.logger,
            self.log_dir
        )
        self.monitor = SystemMonitor()

        # 初始化更新器（使用日志目录的父目录作为客户端目录）
        self.updater = ClientUpdater(self.log_dir.parent)

        # 网络配置（兼容旧配置）
        self._init_network_config()

        self.logger.info(
            f"客户端配置 - 监听端口: {self.client_listen_port}, "
            f"服务端命令端口: {self.server_command_port}, "
            f"服务端监控端口: {self.server_monitor_port}"
        )

    def _init_network_config(self):
        """初始化网络配置，兼容旧版配置格式"""
        if 'command_port' in self.config and 'client_listen_port' not in self.config:
            # 旧配置兼容
            self.client_listen_port = self.config['command_port']
            self.server_command_port = self.config.get('monitor_port', SERVER_COMMAND_PORT)
            self.server_monitor_port = SERVER_MONITOR_PORT
        else:
            # 新配置
            self.client_listen_port = self.config.get('client_listen_port', CLIENT_LISTEN_PORT)
            self.server_command_port = self.config.get('server_command_port', SERVER_COMMAND_PORT)
            self.server_monitor_port = self.config.get('server_monitor_port', SERVER_MONITOR_PORT)

    # ── 生命周期 ──────────────────────────────────────

    def start(self):
        """启动客户端"""
        self.running = True

        # 启动命令端口监听
        threading.Thread(target=self._listen_commands, daemon=True).start()

        # 启动心跳
        threading.Thread(target=self._heartbeat_loop, daemon=True).start()
        self._send_heartbeat_immediate()

        # 启动监控数据上报
        threading.Thread(target=self._monitor_report_loop, daemon=True).start()

        self.logger.info("客户端已启动")

        # 保持主线程运行
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止客户端"""
        self.running = False
        self.logger.info("客户端已停止")

    # ── 网络监听 ──────────────────────────────────────

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

                    if not self.address_pool.is_allowed(addr[0]):
                        self.logger.warning(
                            f"拒绝未授权连接: {addr[0]} "
                            f"(允许的地址: {self.address_pool.allowed_addresses})"
                        )
                        conn.close()
                        continue

                    self.logger.info(f"接受来自 {addr[0]} 的连接")
                    threading.Thread(target=self._handle_connection, args=(conn, addr), daemon=True).start()
                except Exception as e:
                    if self.running:
                        self.logger.error(f"命令端口接受连接错误: {e}")
        except OSError as e:
            self.logger.error(f"命令端口绑定失败 {self.client_listen_port}: {e}")
            self.logger.error("可能端口已被占用，请检查是否有其他实例在运行")
        finally:
            try:
                sock.close()
            except Exception:
                pass

    # ── 消息分发 ──────────────────────────────────────

    def _handle_connection(self, conn: socket.socket, addr: tuple[str, int]):
        """处理新连接：解析消息类型并分发到对应处理器"""
        try:
            msg = recv_json(conn, timeout=CONNECT_TIMEOUT, buffer_size=STREAM_BUFFER_SIZE)
            msg_type = msg.get('type')

            if msg_type == MsgType.COMMAND:
                self._handle_command(conn, addr, msg)
            elif msg_type == MsgType.FILE_UPDATE:
                self._handle_file_transfer(conn, addr, msg)
            elif msg_type == MsgType.UPDATE:
                self._handle_client_update(conn, addr, msg)
            else:
                self.logger.warning(f"未知消息类型: {msg_type} 来自 {addr[0]}")
                send_json(conn, {'status': 'error', 'message': f'未知消息类型: {msg_type}'})
        except (socket.timeout, ConnectionError) as e:
            self.logger.error(f"接收消息失败: {addr[0]} - {e}")
        except json.JSONDecodeError as e:
            self.logger.error(f"消息格式错误: {addr[0]} - {e}")
        except Exception as e:
            self.logger.error(f"处理连接错误: {e}")
            try:
                send_json(conn, {'status': 'error', 'message': str(e)})
            except Exception:
                pass
        finally:
            conn.close()

    # ── 命令处理器 ────────────────────────────────────

    def _handle_command(self, conn: socket.socket, addr: tuple[str, int],
                        msg: dict[str, Any]):
        """处理命令类型消息"""
        command = msg.get('command')
        params = msg.get('params', {})

        handler = self._COMMAND_HANDLERS.get(command)
        if handler:
            result = handler(self, conn, addr, params)
        else:
            result = {'status': 'error', 'message': f'未知命令: {command}'}

        if result is not None:
            send_json(conn, result)
        self.logger.info(f"执行命令: {command}, 结果: {result}")

    def _cmd_clean_log(self, conn, addr, params):
        """清理日志命令"""
        log_date = params.get('date')
        return self.task_executor.clean_log(log_date, self._setup_logging)

    def _cmd_backup(self, conn, addr, params):
        """备份命令：异步执行备份并发送到服务端"""
        server_ip = addr[0]
        result = {'status': 'success', 'message': '备份命令已接收，正在处理...'}
        self.logger.info("执行命令: backup, 已发送初始响应")

        def backup_async():
            try:
                backup_result = self.task_executor.backup_files(
                    server_ip, self.config.get('server_addresses', []),
                    self.server_command_port
                )
                self.logger.info(f"备份完成: {backup_result}")
            except Exception as e:
                self.logger.error(f"备份过程出错: {e}")

        threading.Thread(target=backup_async, daemon=True).start()
        return result

    def _cmd_start_monitor(self, conn, addr, params):
        """启动监控命令"""
        self.monitor.start_monitoring()
        self.logger.info("监控已启动，开始上报数据")
        return {'status': 'success', 'message': '监控已启动'}

    def _cmd_stop_monitor(self, conn, addr, params):
        """停止监控命令"""
        self.monitor.stop_monitoring()
        self.logger.info("监控已停止")
        return {'status': 'success', 'message': '监控已停止'}

    def _cmd_execute_command(self, conn, addr, params):
        """执行远程命令"""
        cmd = params.get('cmd', '')
        timeout = params.get('timeout', COMMAND_TIMEOUT)
        if not cmd:
            return {'status': 'error', 'message': '命令不能为空'}
        self.logger.info(f"执行远程命令: {cmd}")
        result = self.task_executor.execute_command(cmd, timeout)
        self.logger.info(f"命令执行结果: {result.get('return_code', -1)}")
        return result

    def _cmd_get_system_info(self, conn, addr, params):
        """获取系统详细信息"""
        result = self.task_executor.get_system_info()
        result['status'] = 'success'
        self.logger.info(f"获取系统信息: {result.get('hostname', 'unknown')}")
        return result

    def _cmd_get_version(self, conn, addr, params):
        """获取客户端版本"""
        return {
            'status': 'success',
            'version': self.updater.get_local_version()
        }

    def _cmd_get_files_manifest(self, conn, addr, params):
        """获取客户端文件清单"""
        manifest = self.updater.get_local_files_manifest()
        return {'status': 'success', 'manifest': manifest}

    # ── 命令路由表 ────────────────────────────────────

    _COMMAND_HANDLERS = {
        'clean_log':          _cmd_clean_log,
        'backup':             _cmd_backup,
        'start_monitor':      _cmd_start_monitor,
        'stop_monitor':       _cmd_stop_monitor,
        'execute_command':    _cmd_execute_command,
        'get_system_info':    _cmd_get_system_info,
        'get_version':        _cmd_get_version,
        'get_files_manifest': _cmd_get_files_manifest,
    }

    # ── 文件传输处理 ──────────────────────────────────

    def _handle_file_transfer(self, conn: socket.socket, addr: tuple[str, int],
                              msg: dict[str, Any]):
        """处理文件传输消息"""
        remote_path = msg.get('remote_path')
        file_size = msg.get('file_size', 0)
        is_zip = msg.get('is_zip', False)
        update_type = msg.get('update_type', 'single_file')

        # 发送准备就绪
        conn.send('ready'.encode('utf-8'))

        # 接收文件数据
        file_data = self._receive_chunks(conn, file_size, STREAM_BUFFER_SIZE)

        # 保存文件
        result = self.task_executor.update_file(file_data, remote_path, file_size, is_zip)
        send_json(conn, result)
        self.logger.info(f"文件更新 ({update_type}): {remote_path}, 结果: {result}")

    # ── 客户端更新处理 ────────────────────────────────

    def _handle_client_update(self, conn: socket.socket, addr: tuple[str, int],
                              msg: dict[str, Any]):
        """处理客户端更新消息"""
        new_version = msg.get('version')
        update_type = msg.get('update_type', 'incremental')

        self.logger.info(f"收到更新请求: 版本 {new_version}, 类型: {update_type}")

        # 发送准备就绪
        conn.send('ready'.encode('utf-8'))

        # 接收更新数据
        update_data = self._receive_update_data(conn, update_type)
        self.logger.info(f"更新数据接收完成: {len(update_data)} 字节")

        # 处理更新数据
        if update_type == 'incremental':
            result = self._parse_incremental_update(update_data, new_version)
        else:
            result = self.updater.apply_update(update_data, new_version, 'full')

        send_json(conn, result)
        self.logger.info(f"更新结果: {result}")

        # 更新成功后清理旧备份并重启
        if result.get('status') == 'success':
            self.updater.cleanup_old_backups(keep_count=3)
            self._schedule_restart(delay=2)

    def _parse_incremental_update(self, update_data: bytes,
                                  new_version: str) -> dict[str, Any]:
        """解析增量更新数据并应用更新"""
        try:
            encoded_data = json.loads(update_data.decode('utf-8'))
            update_dict = {}
            for file_path, content in encoded_data.items():
                try:
                    update_dict[file_path] = base64.b64decode(content)
                except Exception:
                    update_dict[file_path] = content
            return self.updater.apply_update(update_dict, new_version, 'incremental')
        except Exception as e:
            return {'status': 'error', 'message': f'解析更新数据失败: {str(e)}'}

    # ── 数据接收工具 ──────────────────────────────────

    @staticmethod
    def _receive_chunks(conn: socket.socket, total_size: int,
                        chunk_size: int) -> bytes:
        """按指定块大小接收固定大小的数据"""
        data = b''
        received = 0
        while received < total_size:
            chunk = conn.recv(min(chunk_size, total_size - received))
            if not chunk:
                break
            data += chunk
            received += len(chunk)
        return data

    def _receive_update_data(self, conn: socket.socket,
                             update_type: str) -> bytes:
        """接收更新数据"""
        data = b''
        conn.settimeout(FILE_TRANSFER_TIMEOUT)

        while True:
            try:
                chunk = conn.recv(LARGE_BUFFER_SIZE)
                if not chunk:
                    break
                data += chunk

                if update_type == 'incremental':
                    # 尝试解析JSON判断是否接收完整
                    try:
                        json.loads(data.decode('utf-8'))
                        break
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        continue
                else:
                    # 全量更新：等待连接关闭或超时
                    conn.setblocking(False)
                    try:
                        more = conn.recv(1)
                        if not more:
                            break
                        data += more
                    except BlockingIOError:
                        break
                    finally:
                        conn.setblocking(True)
            except socket.timeout:
                break

        return data

    # ── 心跳 ──────────────────────────────────────────

    def _send_heartbeat_immediate(self):
        """立即发送心跳（用于启动时注册）"""
        for server_ip in self.address_pool.allowed_addresses:
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.settimeout(REGISTER_TIMEOUT)
                sock.connect((server_ip, self.server_command_port))
                send_json(sock, self._build_heartbeat_msg())
                _ = sock.recv(1024)
                sock.close()
                self.logger.info(f"已向服务端 {server_ip} 注册")
            except Exception as e:
                self.logger.warning(f"注册失败 {server_ip}: {e}")

    def _heartbeat_loop(self):
        """心跳循环"""
        time.sleep(2)  # 等待立即心跳先完成
        while self.running:
            try:
                for server_ip in self.address_pool.allowed_addresses:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(REGISTER_TIMEOUT)
                        sock.connect((server_ip, self.server_command_port))
                        send_json(sock, self._build_heartbeat_msg())
                        _ = sock.recv(1024)
                        sock.close()
                    except Exception as e:
                        self.logger.debug(f"心跳发送失败 {server_ip}: {e}")
                time.sleep(HEARTBEAT_INTERVAL)
            except Exception as e:
                self.logger.error(f"心跳循环错误: {e}")
                time.sleep(HEARTBEAT_INTERVAL)

    @staticmethod
    def _build_heartbeat_msg() -> dict[str, Any]:
        """构建心跳消息"""
        return {
            'type': MsgType.HEARTBEAT,
            'os': platform.system(),
            'info': {
                'hostname': platform.node(),
                'os_version': platform.version()
            }
        }

    # ── 监控上报 ──────────────────────────────────────

    def _monitor_report_loop(self):
        """监控数据上报循环"""
        while self.running:
            try:
                if not self.monitor.is_monitoring():
                    self.logger.debug("监控未启动，跳过数据上报")
                    time.sleep(MONITOR_INTERVAL)
                    continue

                monitor_data = self.monitor.get_system_info()
                self.logger.debug(
                    f"准备上报监控数据: "
                    f"CPU={monitor_data.get('cpu_percent', 0):.2f}%, "
                    f"Memory={monitor_data.get('memory_percent', 0):.2f}%"
                )

                for server_ip in self.address_pool.allowed_addresses:
                    try:
                        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        sock.settimeout(CONNECT_TIMEOUT)
                        self.logger.debug(
                            f"连接到服务端 {server_ip}:{self.server_monitor_port} 上报监控数据"
                        )
                        sock.connect((server_ip, self.server_monitor_port))
                        send_json(sock, {
                            'type': MsgType.MONITOR_DATA,
                            'data': monitor_data
                        })
                        self.logger.debug(f"监控数据已发送到 {server_ip}")
                        sock.shutdown(socket.SHUT_WR)
                        sock.close()
                    except Exception as e:
                        self.logger.warning(
                            f"监控数据上报失败 {server_ip}:{self.server_monitor_port} - {e}"
                        )

                time.sleep(MONITOR_INTERVAL)
            except Exception as e:
                self.logger.error(f"监控上报循环错误: {e}")
                time.sleep(MONITOR_INTERVAL)

    # ── 日志 ──────────────────────────────────────────

    def _setup_logging(self):
        """设置日志配置"""
        # 清除现有处理器
        root_logger = logging.getLogger()
        for handler in root_logger.handlers[:]:
            handler.close()
            root_logger.removeHandler(handler)

        for logger_name in logging.root.manager.loggerDict:
            logger_obj = logging.getLogger(logger_name)
            for handler in logger_obj.handlers[:]:
                handler.close()
                logger_obj.removeHandler(handler)

        root_logger.setLevel(logging.DEBUG)

        file_handler = logging.FileHandler(
            self.log_dir / f"{time.strftime('%Y-%m-%d')}.txt", encoding='utf-8'
        )
        stream_handler = logging.StreamHandler()

        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        file_handler.setFormatter(formatter)
        stream_handler.setFormatter(formatter)

        root_logger.addHandler(file_handler)
        root_logger.addHandler(stream_handler)
        self.logger = logging.getLogger(__name__)

    # ── 重启 ──────────────────────────────────────────

    def _schedule_restart(self, delay=2):
        """计划延迟重启客户端"""

        def restart_async():
            time.sleep(delay)
            self.logger.info("正在重启客户端...")

            python_exe = sys.executable
            script_path = Path(__file__).resolve()

            if platform.system() == 'Windows':
                subprocess.Popen(
                    [python_exe, str(script_path)],
                    creationflags=subprocess.CREATE_NEW_CONSOLE,
                    cwd=str(script_path.parent)
                )
            else:
                subprocess.Popen(
                    ['nohup', python_exe, str(script_path), '&'],
                    cwd=str(script_path.parent)
                )

            self.logger.info("客户端即将重启...")
            self.running = False

        threading.Thread(target=restart_async, daemon=True).start()


def main():
    config_path = Path(__file__).parent / 'config.json'

    if not config_path.exists():
        print(f"配置文件不存在: {config_path}")
        print("请确保config.json文件存在于客户端目录中")
        return

    client = Client(config_path)
    client.start()


if __name__ == '__main__':
    main()
