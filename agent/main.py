#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import platform
import sys
import time
from pathlib import Path

import requests

try:
    from .config import AgentConfig
    from .connection import AgentConnection, AgentCredentialRejected, get_local_ip, get_os_info
    from .docker_runtime import DockerRuntime
    from .file_transfer import FileReceiver
    from .monitor import SystemMonitor
    from .task_profiles import TaskProfileStore
    from .task_runner import AgentTaskRunner
    from .updater import AgentUpdater
except ImportError:  # Script execution from the agent directory.
    from config import AgentConfig
    from connection import AgentConnection, AgentCredentialRejected, get_local_ip, get_os_info
    from docker_runtime import DockerRuntime
    from file_transfer import FileReceiver
    from monitor import SystemMonitor
    from task_profiles import TaskProfileStore
    from task_runner import AgentTaskRunner
    from updater import AgentUpdater

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("agent")


class Agent:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.conn = AgentConnection(config)
        self.monitor = SystemMonitor()
        self.docker = DockerRuntime()
        self.file_receiver = FileReceiver(
            config.transfer_root,
            max_bytes=config.max_transfer_bytes,
            chunk_bytes=config.file_chunk_bytes,
            max_total_bytes=config.max_transfer_total_bytes,
            min_free_bytes=config.min_transfer_free_bytes,
            partial_ttl_seconds=config.transfer_partial_ttl_seconds,
        )
        self._last_docker_report = 0.0
        self.task_profiles = TaskProfileStore(config.data_dir / "task_profiles.json")
        self.task_runner = AgentTaskRunner(config, self.task_profiles, self._send_result)
        self.conn.on_message(self._handle_message)
        self.conn.on_connect(self._send_capabilities)
        self.conn.on_disconnect(self.task_runner.shutdown)

    async def _send_result(self, message_type: str, request_id: str, payload: dict) -> bool:
        return await self.conn.send({
            "type": message_type,
            "request_id": request_id,
            "payload": payload,
        })

    async def _handle_message(self, msg: dict):
        msg_type = str(msg.get("type") or "")
        request_id = str(msg.get("request_id") or "")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}

        if msg_type == "task":
            task_type = str(payload.get("task_type") or msg.get("command") or "")
            params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
            error = self.task_runner.validate_request(
                request_id,
                task_id=str(payload.get("task_id") or ""),
                subtask_id=str(payload.get("subtask_id") or ""),
                task_type=task_type,
                params=params,
            )
            accepted = error is None
            acknowledged = await self._send_result("task_ack", request_id, {
                "accepted": accepted,
                "error": error,
            })
            if accepted and acknowledged:
                started, start_error = self.task_runner.accept(
                    request_id,
                    task_id=str(payload.get("task_id") or ""),
                    subtask_id=str(payload.get("subtask_id") or ""),
                    task_type=task_type,
                    params=params,
                )
                if not started:
                    await self._send_result("task_result", request_id, {
                        "success": False,
                        "status": "failed",
                        "error": start_error or "task could not be started",
                        "message": "任务启动失败",
                        "result": {},
                    })

        elif msg_type == "task_cancel":
            cancelled = await self.task_runner.cancel(request_id)
            await self._send_result("task_cancel_ack", request_id, {
                "cancelled": cancelled,
            })

        elif msg_type == "container_action":
            try:
                result = await self.docker.action(
                    str(payload.get("runtime_id") or ""), str(payload.get("action") or ""),
                )
            except Exception as exc:
                result = {"success": False, "error": str(exc)}
            await self._send_result("container_result", request_id, result)

        elif msg_type == "container_logs":
            try:
                result = await self.docker.logs(
                    str(payload.get("runtime_id") or ""), int(payload.get("tail", 200)),
                )
            except Exception as exc:
                result = {"success": False, "error": str(exc), "logs": ""}
            await self._send_result("container_logs_result", request_id, result)

        elif msg_type == "file_transfer_init":
            result = self.file_receiver.init(payload)
            await self._send_result("file_transfer_result", request_id, result)

        elif msg_type == "file_transfer_chunk":
            result = self.file_receiver.chunk(payload)
            await self._send_result("file_transfer_result", request_id, result)

        elif msg_type == "file_transfer_commit":
            result = self.file_receiver.commit(payload)
            await self._send_result("file_transfer_result", request_id, result)

        elif msg_type == "file_transfer_cancel":
            result = self.file_receiver.cancel(payload)
            await self._send_result("file_transfer_result", request_id, result)

        elif msg_type == "update":
            backup_dir = self.config.data_dir / "backup" / f"backup_{time.strftime('%Y%m%d_%H%M%S')}"
            updater = AgentUpdater(Path(__file__).parent, backup_dir)
            result = updater.apply_update(payload)
            await self._send_result("update_result", request_id, result)

        elif msg_type == "ping":
            await self.conn.send({"type": "pong", "request_id": request_id, "payload": {}})

    async def _send_capabilities(self) -> None:
        self.task_profiles.reload_if_changed()
        await self.conn.send({
            "type": "agent_capabilities",
            "payload": self.task_profiles.capabilities(),
        })

    async def enroll(self) -> bool:
        if not self.config.enrollment_token:
            logger.error("No valid Agent credential. Set WCM_ENROLLMENT_TOKEN to enroll this device.")
            return False
        docker_available = await self.docker.available()
        system = platform.system().lower()
        platform_type = "docker-host" if docker_available else (system if system in {"windows", "linux"} else "unknown")
        body = {
            "agent_id": self.config.agent_id,
            "ip": self.config.client_ip or get_local_ip(),
            "hostname": self.config.hostname or platform.node() or "unknown",
            "os": self.config.os_info or get_os_info(),
            "platform": platform_type,
            "version": self.config.version,
            "capabilities": {
                "docker": docker_available,
                "remote_commands": self.config.enable_remote_commands,
                "file_transfer_v2": True,
                **self.task_profiles.capabilities(),
            },
        }

        def request_enrollment():
            return requests.post(
                f"{self.config.api_url}/agents/enroll",
                headers={"Authorization": f"Bearer {self.config.enrollment_token}"},
                json=body, timeout=15, verify=self.config.ca_cert or True,
            )

        try:
            response = await asyncio.to_thread(request_enrollment)
            response.raise_for_status()
            data = response.json()
            self.config.node_id = data["node_id"]
            self.config.agent_token = data["agent_token"]
            self.config.platform = platform_type
            self.config.enrollment_token = ""
            os.environ.pop("WCM_ENROLLMENT_TOKEN", None)
            self.config.save(self.config.data_dir / "agent_config.json")
            logger.info("Securely enrolled as node %s", self.config.node_id)
            return True
        except Exception:
            logger.exception("Secure enrollment failed")
            return False

    async def _telemetry_loop(self):
        self.monitor.start()
        while self.conn._running:
            await asyncio.sleep(self.config.monitor_interval)
            if not self.conn._ws:
                continue
            try:
                await self.conn.send({"type": "monitor_data", "payload": self.monitor.collect()})
                now = time.monotonic()
                if now - self._last_docker_report >= self.config.docker_interval:
                    inventory = await self.docker.list_containers()
                    await self.conn.send({"type": "container_inventory", "payload": {"containers": inventory}})
                    self._last_docker_report = now
                if self.task_profiles.reload_if_changed():
                    if self.task_profiles.last_error:
                        logger.error("Task profile reload failed: %s", self.task_profiles.last_error)
                    await self._send_capabilities()
            except Exception:
                logger.exception("Telemetry collection failed")

    async def run(self) -> bool:
        if not self.config.agent_token and not await self.enroll():
            return False
        while True:
            self.conn._running = True
            telemetry = asyncio.create_task(self._telemetry_loop())
            try:
                await self.conn.run_forever()
            except AgentCredentialRejected:
                logger.error("Agent credential was revoked; re-enrollment is required")
                self.config.node_id = ""
                self.config.agent_token = ""
                self.config.save(self.config.data_dir / "agent_config.json")
                return False
            finally:
                telemetry.cancel()
                try:
                    await telemetry
                except asyncio.CancelledError:
                    pass


def build_config(args: argparse.Namespace) -> AgentConfig:
    initial = AgentConfig()
    if args.data_dir:
        initial.data_dir = Path(args.data_dir)
        initial.transfer_root = initial.data_dir / "transfers"
    config_path = initial.data_dir / "agent_config.json"
    config = AgentConfig.load(config_path)
    config.data_dir = initial.data_dir
    config.transfer_root = initial.transfer_root
    config.enrollment_token = os.getenv("WCM_ENROLLMENT_TOKEN", "")
    # Remote commands require an explicit opt-in on every Agent start. Capacity
    # and concurrency environment variables intentionally override persisted
    # defaults so operators can change limits without editing credentials.
    config.enable_remote_commands = (
        os.getenv("WCM_ENABLE_REMOTE_COMMANDS", "false").lower() == "true"
    )
    integer_overrides = {
        "WCM_TASK_CONCURRENCY": "task_concurrency",
        "WCM_TASK_BACKUP_RETENTION_DAYS": "task_backup_retention_days",
        "WCM_TASK_BACKUP_TOTAL_BYTES": "task_backup_total_bytes",
        "WCM_TASK_BACKUP_MIN_FREE_BYTES": "task_backup_min_free_bytes",
    }
    for environment_name, attribute in integer_overrides.items():
        if environment_name in os.environ:
            setattr(config, attribute, int(os.environ[environment_name]))
    if args.server:
        config.server_url = args.server
    if args.api:
        config.api_url = args.api
    if args.ca_cert:
        config.ca_cert = args.ca_cert
    config.client_ip = get_local_ip()
    config.hostname = platform.node()
    config.os_info = get_os_info()
    return config


def main() -> int:
    parser = argparse.ArgumentParser(description="Web Cluster Manager Agent")
    parser.add_argument("--server", default=None, help="wss://server/ws/agent")
    parser.add_argument("--api", default=None, help="https://server/api/v2")
    parser.add_argument("--ca-cert", default=None, help="internal CA certificate path")
    parser.add_argument("--data-dir", default=None, help="persistent credential and runtime directory")
    parser.add_argument("--enroll-only", action="store_true", help="enroll securely, save credential, then exit")
    args = parser.parse_args()
    agent = Agent(build_config(args))
    try:
        if args.enroll_only:
            return 0 if asyncio.run(agent.enroll()) else 1
        return 0 if asyncio.run(agent.run()) else 1
    except KeyboardInterrupt:
        logger.info("Agent stopped")
        return 0


if __name__ == "__main__":
    sys.exit(main())
