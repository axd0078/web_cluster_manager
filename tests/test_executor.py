from __future__ import annotations

import asyncio
import json
import sys
import uuid

from agent.config import AgentConfig
from agent.task_profiles import TaskProfileStore
from agent.task_runner import AgentTaskRunner


def _write_profiles(path, command_profiles):
    path.write_text(json.dumps({
        "version": 1,
        "log_profiles": {},
        "backup_profiles": {},
        "service_profiles": {},
        "command_profiles": command_profiles,
    }), encoding="utf-8")


def test_fixed_argv_command_rejects_parameter_injection_and_bounds_output(tmp_path):
    profile_path = tmp_path / "task_profiles.json"
    _write_profiles(profile_path, {
        "bounded": {
            "argv": [
                sys.executable,
                "-c",
                "import sys; sys.stdout.buffer.write(b'x' * 200000)",
            ],
            "timeout": 10,
        },
    })
    config = AgentConfig(
        data_dir=tmp_path,
        enable_remote_commands=True,
    )
    store = TaskProfileStore(profile_path)
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        accepted, error = runner.accept(
            uuid.uuid4().hex,
            task_id=uuid.uuid4().hex,
            subtask_id=uuid.uuid4().hex,
            task_type="batch_command",
            params={"profile": "bounded", "command": "whoami"},
        )
        assert accepted is False
        assert "unsupported parameters" in str(error)

        execution_id = uuid.uuid4().hex
        accepted, error = runner.accept(
            execution_id,
            task_id=uuid.uuid4().hex,
            subtask_id=uuid.uuid4().hex,
            task_type="batch_command",
            params={"profile": "bounded"},
        )
        assert accepted is True and error is None
        await runner._running[execution_id][0]

    asyncio.run(scenario())
    result = [item for item in messages if item[0] == "task_result"][-1][2]
    assert result["status"] == "failed"
    assert "64 KiB" in result["error"]


def test_command_cancellation_terminates_child_process(tmp_path):
    profile_path = tmp_path / "task_profiles.json"
    _write_profiles(profile_path, {
        "slow": {
            "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
            "timeout": 60,
        },
    })
    config = AgentConfig(data_dir=tmp_path, enable_remote_commands=True)
    store = TaskProfileStore(profile_path)
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        execution_id = uuid.uuid4().hex
        accepted, _ = runner.accept(
            execution_id,
            task_id=uuid.uuid4().hex,
            subtask_id=uuid.uuid4().hex,
            task_type="batch_command",
            params={"profile": "slow"},
        )
        assert accepted
        await asyncio.sleep(0.2)
        assert await runner.cancel(execution_id) is True

    asyncio.run(scenario())
    result = [item for item in messages if item[0] == "task_result"][-1][2]
    assert result["status"] == "cancelled"
