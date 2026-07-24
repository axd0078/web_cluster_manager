from __future__ import annotations

import logging

logger = logging.getLogger("agent.monitor")


class SystemMonitor:
    """Collects system metrics using psutil."""

    def __init__(self):
        self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start(self):
        self._enabled = True

    def stop(self):
        self._enabled = False

    @staticmethod
    def collect() -> dict:
        import psutil
        cpu = psutil.cpu_percent(interval=0.5)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        return {
            "cpu_percent": cpu,
            "mem_percent": mem.percent,
            "mem_used": mem.used,
            "mem_total": mem.total,
            "disk_percent": disk.percent,
            "disk_used": disk.used,
            "disk_total": disk.total,
        }
