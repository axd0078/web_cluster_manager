#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Web 集群管理系统 — Agent（WebSocket 客户端）

通过 WebSocket 长连接与服务端通信：
- 注册节点 → 定期心跳 → 上报监控数据 → 接收命令 → 执行并回传结果
- 断线自动重连（指数退避）
"""

import asyncio
import json
import logging
import os
import platform
import signal
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import websockets
from websockets.asyncio.client import connect as ws_connect
from websockets.exceptions import ConnectionClosed

from modules.system_monitor import SystemMonitor
from modules.task_executor import TaskExecutor

# ── 日志 ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] agent: %(message)s",
)
logger = logging.getLogger("agent")


# ── Agent 类 ──

class Agent:
    """WebSocket Agent — 复用 client_new/core/ 的业务逻辑。"""

    def __init__(self, config_path: str = "config.json") -> None:
        config_path = Path(config_path)
        if not config_path.exists():
            config_path = Path(__file__).parent / "config.json"
        with open(config_path, "r", encoding="utf-8") as f:
            self.cfg: dict[str, Any] = json.load(f)

        self.server_url: str = self.cfg["server_url"]
        self.node_id: str = self.cfg["node_id"]
        self.token: str = self.cfg["token"]
        self.heartbeat_interval: int = self.cfg.get("heartbeat_interval", 10)
        self.monitor_interval: int = self.cfg.get("monitor_interval", 5)
        self.reconnect_min: int = self.cfg.get("reconnect_min_delay", 1)
        self.reconnect_max: int = self.cfg.get("reconnect_max_delay", 60)

        # 复用现有组件
        self.monitor = SystemMonitor()
        self.executor = TaskExecutor(
            backup_path=str(Path(config_path).parent / "backup"),
            web_app_path=str(Path(config_path).parent / "web_app"),
            logger=logger,
            log_dir=str(Path(config_path).parent / "log"),
        )

        # 运行时状态
        self._running = False
        self._ws: Any = None
        self._reconnect_delay = self.reconnect_min

        # 从 node_id 推断 hostname（可被注册响应覆盖）
        self.hostname = platform.node()

    # ── 连接管理 ──

    async def connect(self) -> None:
        """建立 WebSocket 连接到服务端。"""
        url = f"{self.server_url}/ws/agent/{self.node_id}?token={self.token}"
        logger.info(f"Connecting to {url}")
        self._ws = await ws_connect(url)
        logger.info("Connected to server")

    async def disconnect(self) -> None:
        """关闭连接。"""
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

    # ── 消息收发 ──

    async def send(self, msg_type: str, payload: dict[str, Any]) -> None:
        """发送 JSON 消息。"""
        if not self._ws:
            return
        msg = {
            "type": msg_type,
            "payload": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        try:
            await self._ws.send(json.dumps(msg))
        except Exception as e:
            logger.error(f"Send error: {e}")

    async def recv(self) -> dict[str, Any] | None:
        """接收一条 JSON 消息。"""
        if not self._ws:
            return None
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=30)
            return json.loads(raw)
        except asyncio.TimeoutError:
            return None
        except ConnectionClosed:
            return None

    # ── 心跳 ──

    async def heartbeat_loop(self) -> None:
        """定期发送心跳（含基础监控数据）。"""
        while self._running:
            try:
                # 收集当前监控数据
                sys_info = {}
                try:
                    sys_info = self.monitor.get_system_info()
                except Exception:
                    pass

                await self.send("heartbeat", {
                    "hostname": self.hostname,
                    "os": platform.system(),
                    "version": "2.0.0",
                    "cpu_percent": sys_info.get("cpu_percent", 0),
                    "memory_percent": sys_info.get("memory_percent", 0),
                    "disk_percent": sys_info.get("disk_percent", 0),
                })
                logger.debug(f"Heartbeat sent (CPU: {sys_info.get('cpu_percent', 0):.1f}%)")
            except Exception as e:
                logger.error(f"Heartbeat error: {e}")
            await asyncio.sleep(self.heartbeat_interval)

    # ── 监控上报 ──

    async def monitor_loop(self) -> None:
        """定期上报详细监控数据。"""
        await asyncio.sleep(2)  # 启动后等 2 秒再开始
        while self._running:
            try:
                if self.monitor.is_monitoring():
                    data = self.monitor.get_system_info()
                else:
                    data = self.monitor.get_system_info()
                await self.send("monitor_data", data)
            except Exception as e:
                logger.error(f"Monitor error: {e}")
            await asyncio.sleep(self.monitor_interval)

    # ── 命令处理 ──

    async def handle_command(self, msg: dict[str, Any]) -> None:
        """执行命令并将结果发送回服务端。"""
        payload = msg.get("payload", {})
        command = payload.get("command", "")
        params = payload.get("params", {})
        request_id = payload.get("request_id", "")

        logger.info(f"Executing command: {command} (request_id={request_id})")

        try:
            result: dict[str, Any]
            if command == "get_system_info":
                result = self.executor.get_system_info()
                result["status"] = "success"
            elif command == "execute_command":
                cmd = params.get("cmd", "")
                timeout = params.get("timeout", 30)
                result = self.executor.execute_command(cmd, timeout)
            elif command == "start_monitor":
                self.monitor.start_monitoring()
                result = {"status": "success", "message": "Monitoring started"}
            elif command == "stop_monitor":
                self.monitor.stop_monitoring()
                result = {"status": "success", "message": "Monitoring stopped"}
            elif command == "get_version":
                result = {"status": "success", "version": "2.0.0"}
            else:
                result = {"status": "error", "message": f"Unknown command: {command}"}

            await self.send("command_result", {
                "request_id": request_id,
                "command": command,
                "result": result,
            })
            logger.info(f"Command {command} completed: {result.get('status', 'unknown')}")
        except Exception as e:
            logger.error(f"Command {command} failed: {e}")
            await self.send("command_result", {
                "request_id": request_id,
                "command": command,
                "result": {"status": "error", "message": str(e)},
            })

    # ── 主循环 ──

    async def run(self) -> None:
        """Agent 主循环：连接 → 注册 → 接收消息。"""
        self._running = True
        self._reconnect_delay = self.reconnect_min

        while self._running:
            try:
                # 1. 建立连接
                await self.connect()

                # 2. 发送注册消息
                await self.send("register", {
                    "hostname": self.hostname,
                    "os": platform.system(),
                    "version": "2.0.0",
                })
                logger.info(f"Registered as {self.node_id}")

                # 3. 启动后台任务
                heartbeat_task = asyncio.create_task(self.heartbeat_loop())
                monitor_task = asyncio.create_task(self.monitor_loop())

                # 4. 重置重连延迟
                self._reconnect_delay = self.reconnect_min

                # 5. 消息接收循环
                while self._running:
                    msg = await self.recv()
                    if msg is None and self._ws is None:
                        break  # 连接丢失

                    if msg:
                        msg_type = msg.get("type", "")
                        if msg_type == "command":
                            asyncio.create_task(self.handle_command(msg))

                # 清理
                heartbeat_task.cancel()
                monitor_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                try:
                    await monitor_task
                except asyncio.CancelledError:
                    pass

            except (ConnectionClosed, OSError, websockets.exceptions.ConnectionClosedError) as e:
                logger.warning(f"Connection lost: {e}")
            except Exception as e:
                logger.error(f"Unexpected error: {e}")

            # 断开连接
            await self.disconnect()

            # 重连等待
            if self._running:
                logger.info(f"Reconnecting in {self._reconnect_delay}s...")
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(self._reconnect_delay * 2, self.reconnect_max)

    def stop(self) -> None:
        """停止 Agent。"""
        self._running = False


# ── 入口 ──

async def main() -> None:
    agent = Agent()

    # 信号处理
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, agent.stop)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass

    try:
        await agent.run()
    except KeyboardInterrupt:
        agent.stop()
    finally:
        await agent.disconnect()
        logger.info("Agent stopped")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
