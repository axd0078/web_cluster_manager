from __future__ import annotations

import importlib.util
import asyncio
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
