from __future__ import annotations

import importlib.util
import asyncio
import sys
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).parents[1] / "agent" / "docker_runtime.py"
SPEC = importlib.util.spec_from_file_location("wcm_docker_runtime", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)
DockerRuntime = MODULE.DockerRuntime


def test_docker_actions_are_allowlisted(monkeypatch):
    runtime = DockerRuntime("docker")

    async def fake_run(*args, **kwargs):
        return 0, args[-1], ""

    monkeypatch.setattr(runtime, "_run", fake_run)
    assert asyncio.run(runtime.action("a" * 64, "restart"))["success"] is True
    with pytest.raises(ValueError):
        asyncio.run(runtime.action("a" * 64, "exec"))
    with pytest.raises(ValueError):
        asyncio.run(runtime.action("../../etc/passwd", "stop"))


def test_docker_unavailable_degrades_to_empty_inventory():
    runtime = DockerRuntime("")
    assert asyncio.run(runtime.list_containers()) == []


def test_sensitive_docker_labels_are_not_reported():
    labels = DockerRuntime._labels("app=demo,api_token=hidden,db_password=hidden,team=ops")
    assert labels == {"app": "demo", "team": "ops"}


def test_docker_process_output_is_bounded_before_decode():
    runtime = DockerRuntime(sys.executable)
    code, stdout, stderr = asyncio.run(runtime._run(
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 200000)",
        timeout=10,
        max_output_bytes=1024,
    ))
    assert code == 125
    assert stdout == ""
    assert "exceeded byte limit" in stderr
