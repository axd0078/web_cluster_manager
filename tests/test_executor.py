from __future__ import annotations

import asyncio
import subprocess
import sys

from executor import CommandExecutor


def test_allowlisted_command_output_is_bounded_before_decode():
    command = subprocess.list2cmdline([
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 200000)",
    ])
    executor = CommandExecutor(enabled=True, allowlist=[command])
    result = asyncio.run(executor.execute(command, timeout=10))
    assert result["success"] is False
    assert "64 KiB" in result["error"]
    assert result["stdout"] == ""
    assert result["stderr"] == ""
