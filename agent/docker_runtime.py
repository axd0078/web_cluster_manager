from __future__ import annotations

import asyncio
import json
import re
import shutil

try:
    from .process_output import (
        ProcessExecutionTimeout,
        ProcessOutputLimitError,
        communicate_bounded,
    )
except ImportError:  # Script execution from the agent directory.
    from process_output import (
        ProcessExecutionTimeout,
        ProcessOutputLimitError,
        communicate_bounded,
    )

_CONTAINER_ID = re.compile(r"^[0-9a-fA-F]{12,64}$")


class DockerRuntime:
    ACTIONS = {"start", "stop", "restart"}

    def __init__(self, executable: str | None = None):
        # None means auto-discover; an explicit empty string disables Docker.
        # This makes the unavailable/degraded mode deterministic for operators
        # and tests even when a Docker CLI happens to be installed.
        self.executable = (shutil.which("docker") or "") if executable is None else executable

    async def _run(
        self, *args: str, timeout: float = 15, max_output_bytes: int = 2 * 1024 * 1024,
    ) -> tuple[int, str, str]:
        if not self.executable:
            return 127, "", "docker CLI not found"
        process = await asyncio.create_subprocess_exec(
            self.executable, *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await communicate_bounded(
                process, timeout=timeout, max_output_bytes=max_output_bytes,
            )
        except ProcessExecutionTimeout:
            return 124, "", "docker command timed out"
        except ProcessOutputLimitError:
            return 125, "", "docker command output exceeded byte limit"
        return (
            process.returncode or 0,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )

    async def available(self) -> bool:
        code, _, _ = await self._run("version", "--format", "{{.Server.Version}}", timeout=5)
        return code == 0

    @staticmethod
    def _percent(value: object) -> float | None:
        try:
            return float(str(value).strip().rstrip("%"))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _labels(value: object) -> dict[str, str]:
        labels: dict[str, str] = {}
        for part in str(value or "").split(","):
            key, separator, item = part.partition("=")
            if separator and key:
                lowered = key.lower()
                if any(word in lowered for word in ("secret", "token", "password", "credential", "auth", "private_key")):
                    continue
                labels[key[:100]] = item[:500]
        return labels

    async def list_containers(self) -> list[dict]:
        if not await self.available():
            return []
        ps_code, ps_output, ps_error = await self._run(
            "ps", "-a", "--no-trunc", "--format", "{{json .}}", timeout=15,
        )
        if ps_code != 0:
            raise RuntimeError(ps_error.strip() or "docker ps failed")
        stats_code, stats_output, _ = await self._run(
            "stats", "--no-stream", "--format", "{{json .}}", timeout=20,
        )
        stats_by_id: dict[str, dict] = {}
        if stats_code == 0:
            for line in stats_output.splitlines():
                try:
                    item = json.loads(line)
                    stats_by_id[str(item.get("ID", ""))] = item
                except json.JSONDecodeError:
                    continue

        containers: list[dict] = []
        for line in ps_output.splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            runtime_id = str(item.get("ID") or "")
            stats = stats_by_id.get(runtime_id) or stats_by_id.get(runtime_id[:12]) or {}
            containers.append({
                "runtime_id": runtime_id,
                "name": str(item.get("Names") or runtime_id[:12]),
                "image": str(item.get("Image") or ""),
                "state": str(item.get("State") or "unknown").lower(),
                "status": str(item.get("Status") or ""),
                "labels": self._labels(item.get("Labels")),
                "cpu_percent": self._percent(stats.get("CPUPerc")),
                "mem_percent": self._percent(stats.get("MemPerc")),
                "mem_usage": str(stats.get("MemUsage") or "")[:100] or None,
            })
        return containers

    @staticmethod
    def _validate_id(runtime_id: str) -> None:
        if not _CONTAINER_ID.fullmatch(runtime_id):
            raise ValueError("invalid Docker container id")

    async def action(self, runtime_id: str, action: str) -> dict:
        self._validate_id(runtime_id)
        if action not in self.ACTIONS:
            raise ValueError("unsupported Docker action")
        code, stdout, stderr = await self._run(
            action, runtime_id, timeout=30, max_output_bytes=64 * 1024,
        )
        return {
            "success": code == 0,
            "output": stdout.strip()[-10_000:],
            "error": stderr.strip()[-10_000:] if code else "",
        }

    async def logs(self, runtime_id: str, tail: int = 200) -> dict:
        self._validate_id(runtime_id)
        tail = max(1, min(int(tail), 1000))
        code, stdout, stderr = await self._run(
            "logs", "--tail", str(tail), runtime_id,
            timeout=20, max_output_bytes=220_000,
        )
        combined = (stdout + stderr)[-200_000:]
        return {"success": code == 0, "logs": combined, "error": "" if code == 0 else stderr[-10_000:]}
