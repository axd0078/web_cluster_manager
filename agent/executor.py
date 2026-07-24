from __future__ import annotations

import asyncio
import platform


class CommandExecutor:
    def __init__(self, enabled: bool = False, allowlist: list[str] | None = None):
        self.enabled = enabled
        self.allowlist = {item.strip() for item in (allowlist or []) if item.strip()}

    def is_allowed(self, command: str) -> bool:
        return self.enabled and bool(command) and command.strip() in self.allowlist

    async def execute(self, command: str, timeout: int = 30) -> dict:
        command = command.strip()
        if not self.is_allowed(command):
            return {"success": False, "error": "远程命令已禁用或不在精确白名单中", "stdout": "", "stderr": ""}
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=max(1, min(timeout, 300)))
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            return {"success": False, "error": "命令执行超时", "stdout": "", "stderr": ""}
        encoding = "gbk" if platform.system() == "Windows" else "utf-8"
        return {
            "success": process.returncode == 0,
            "returncode": process.returncode,
            "stdout": stdout.decode(encoding, errors="replace")[-10_000:],
            "stderr": stderr.decode(encoding, errors="replace")[-10_000:],
        }

    @staticmethod
    async def get_system_info() -> dict:
        return {
            "hostname": platform.node(),
            "os": f"{platform.system()} {platform.release()}",
            "python": platform.python_version(),
            "machine": platform.machine(),
        }
