from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
import uuid
import zipfile
from types import SimpleNamespace

import pytest

from agent.config import AgentConfig
from agent.task_profiles import TaskProfileStore
from agent.task_profiles import ServiceProfile
from agent.task_runner import AgentTaskRunner


def _write_profiles(path, *, log_root, backup_root):
    path.write_text(json.dumps({
        "version": 1,
        "log_profiles": {
            "application": {
                "root": str(log_root),
                "patterns": ["*.log", "*.log.*"],
            },
        },
        "backup_profiles": {"application": {"root": str(backup_root)}},
        "service_profiles": {},
        "command_profiles": {},
    }), encoding="utf-8")


async def _run_task(runner, messages, task_type, params):
    execution_id = uuid.uuid4().hex
    accepted, error = runner.accept(
        execution_id,
        task_id=uuid.uuid4().hex,
        subtask_id=uuid.uuid4().hex,
        task_type=task_type,
        params=params,
    )
    assert accepted, error
    await runner._running[execution_id][0]
    return [
        payload for message_type, request_id, payload in messages
        if message_type == "task_result" and request_id == execution_id
    ][-1]


def test_profiles_hot_reload_and_fail_closed(tmp_path):
    profiles = tmp_path / "task_profiles.json"
    _write_profiles(profiles, log_root=tmp_path / "logs", backup_root=tmp_path / "data")
    store = TaskProfileStore(profiles)
    assert store.capabilities()["task_profiles"]["clean_logs"] == ["application"]

    time.sleep(0.01)
    profiles.write_text('{"version": 3}', encoding="utf-8")
    assert store.reload_if_changed() is True
    assert store.last_error
    assert store.capabilities()["task_profiles"]["clean_logs"] == []


def test_agent_environment_overrides_task_limits_and_remote_command_opt_in(
    tmp_path, monkeypatch,
):
    from agent import main as agent_main

    persisted = AgentConfig(
        data_dir=tmp_path,
        transfer_root=tmp_path / "transfers",
        enable_remote_commands=True,
        task_concurrency=2,
        task_backup_total_bytes=2048,
    )
    persisted.save(tmp_path / "agent_config.json")
    monkeypatch.delenv("WCM_ENABLE_REMOTE_COMMANDS", raising=False)
    monkeypatch.setenv("WCM_TASK_CONCURRENCY", "4")
    monkeypatch.setenv("WCM_TASK_BACKUP_TOTAL_BYTES", "4096")
    monkeypatch.setattr(agent_main, "get_local_ip", lambda: "127.0.0.1")
    config = agent_main.build_config(SimpleNamespace(
        data_dir=str(tmp_path),
        server=None,
        api=None,
        ca_cert=None,
    ))
    assert config.enable_remote_commands is False
    assert config.task_concurrency == 4
    assert config.task_backup_total_bytes == 4096


def test_log_preview_and_formal_cleanup_are_profile_sandboxed(tmp_path):
    log_root = tmp_path / "logs"
    backup_root = tmp_path / "data"
    log_root.mkdir()
    backup_root.mkdir()
    old_log = log_root / "old.log"
    current_log = log_root / "current.log"
    ignored = log_root / "old.txt"
    for path in (old_log, current_log, ignored):
        path.write_text(path.name, encoding="utf-8")
    old_time = time.time() - 10 * 86400
    os.utime(old_log, (old_time, old_time))
    os.utime(ignored, (old_time, old_time))

    profiles = tmp_path / "task_profiles.json"
    _write_profiles(profiles, log_root=log_root, backup_root=backup_root)
    store = TaskProfileStore(profiles)
    config = AgentConfig(data_dir=tmp_path)
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        preview = await _run_task(runner, messages, "clean_logs", {
            "profile": "application",
            "older_than_days": 7,
            "dry_run": True,
        })
        assert preview["status"] == "completed"
        assert preview["result"]["candidate_count"] == 1
        assert preview["result"]["sample_paths"] == ["old.log"]
        assert old_log.exists()

        formal = await _run_task(runner, messages, "clean_logs", {
            "profile": "application",
            "older_than_days": 7,
            "dry_run": False,
            "preview_task_id": uuid.uuid4().hex,
        })
        assert formal["status"] == "completed"
        assert formal["result"]["deleted_count"] == 1

    asyncio.run(scenario())
    assert not old_log.exists()
    assert current_log.exists()
    assert ignored.exists()


def test_backup_zip_is_atomic_hashed_and_rejects_traversal(tmp_path):
    log_root = tmp_path / "logs"
    source_root = tmp_path / "source"
    payload_dir = source_root / "payload"
    log_root.mkdir()
    payload_dir.mkdir(parents=True)
    (payload_dir / "a.txt").write_text("alpha", encoding="utf-8")
    (payload_dir / "b.bin").write_bytes(b"\x00\x01\x02")
    profiles = tmp_path / "task_profiles.json"
    _write_profiles(profiles, log_root=log_root, backup_root=source_root)
    store = TaskProfileStore(profiles)
    config = AgentConfig(
        data_dir=tmp_path,
        task_backup_total_bytes=10 * 1024 * 1024,
        task_backup_min_free_bytes=0,
    )
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        result = await _run_task(runner, messages, "backup_files", {
            "profile": "application",
            "source": "payload",
        })
        assert result["status"] == "completed"
        relative = result["result"]["path"]
        archive = tmp_path / relative
        assert archive.exists()
        assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["result"]["sha256"]
        with zipfile.ZipFile(archive) as bundle:
            assert sorted(bundle.namelist()) == ["payload/a.txt", "payload/b.bin"]
            assert bundle.read("payload/a.txt") == b"alpha"
        assert not list((tmp_path / "task_backups").glob("*.tmp"))

        traversal = await _run_task(runner, messages, "backup_files", {
            "profile": "application",
            "source": "../outside",
        })
        assert traversal["status"] == "failed"
        assert "relative" in traversal["error"]

    asyncio.run(scenario())


def test_task_file_operations_reject_detected_symbolic_links(tmp_path, monkeypatch):
    log_root = tmp_path / "logs"
    source_root = tmp_path / "source"
    linked_source = source_root / "linked"
    log_root.mkdir()
    linked_source.mkdir(parents=True)
    linked_log = log_root / "linked.log"
    linked_log.write_text("must not be touched", encoding="utf-8")
    old_time = time.time() - 10 * 86400
    os.utime(linked_log, (old_time, old_time))
    profiles = tmp_path / "task_profiles.json"
    _write_profiles(profiles, log_root=log_root, backup_root=source_root)
    store = TaskProfileStore(profiles)
    config = AgentConfig(data_dir=tmp_path, task_backup_min_free_bytes=0)
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        monkeypatch.setattr(
            "agent.task_runner._is_link",
            lambda path: path.name in {"linked", "linked.log"},
        )
        preview = await _run_task(runner, messages, "clean_logs", {
            "profile": "application",
            "older_than_days": 7,
            "dry_run": True,
        })
        assert preview["status"] == "completed"
        assert preview["result"]["candidate_count"] == 0
        assert linked_log.exists()

        backup = await _run_task(runner, messages, "backup_files", {
            "profile": "application",
            "source": "linked",
        })
        assert backup["status"] == "failed"
        assert "symbolic links" in backup["error"]

    asyncio.run(scenario())


def test_backup_capacity_refuses_new_archive_without_deleting_unexpired(tmp_path):
    source_root = tmp_path / "source"
    source_root.mkdir()
    (source_root / "large.bin").write_bytes(b"x" * 4096)
    profiles = tmp_path / "task_profiles.json"
    _write_profiles(profiles, log_root=tmp_path, backup_root=source_root)
    backup_dir = tmp_path / "task_backups"
    backup_dir.mkdir()
    existing = backup_dir / "existing.zip"
    existing.write_bytes(b"z" * 900)
    config = AgentConfig(
        data_dir=tmp_path,
        task_backup_total_bytes=1024,
        task_backup_min_free_bytes=0,
        task_backup_retention_days=90,
    )
    store = TaskProfileStore(profiles)
    messages: list[tuple[str, str, dict]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(config, store, sender)
        result = await _run_task(runner, messages, "backup_files", {
            "profile": "application",
            "source": "large.bin",
        })
        assert result["status"] == "failed"
        assert "capacity" in result["error"]

    asyncio.run(scenario())
    assert existing.exists()


@pytest.mark.parametrize(
    ("system_name", "profile", "responses", "expected"),
    [
        (
            "Linux",
            ServiceProfile(manager="systemd", name="nginx.service"),
            [
                (0, "active\n", ""),
                (0, "", ""),
                (0, "active\n", ""),
            ],
            [
                ("systemctl", "is-active", "nginx.service"),
                ("systemctl", "restart", "nginx.service"),
                ("systemctl", "is-active", "nginx.service"),
            ],
        ),
        (
            "Windows",
            ServiceProfile(manager="windows_service", name="Spooler"),
            [
                (0, "STATE : 4 RUNNING", ""),
                (0, "", ""),
                (0, "STATE : 1 STOPPED", ""),
                (0, "", ""),
                (0, "STATE : 4 RUNNING", ""),
            ],
            [
                ("sc.exe", "query", "Spooler"),
                ("sc.exe", "stop", "Spooler"),
                ("sc.exe", "query", "Spooler"),
                ("sc.exe", "start", "Spooler"),
                ("sc.exe", "query", "Spooler"),
            ],
        ),
    ],
)
def test_service_restart_uses_exact_argv_and_polls_state(
    tmp_path, monkeypatch, system_name, profile, responses, expected,
):
    profile_path = tmp_path / "task_profiles.json"
    _write_profiles(profile_path, log_root=tmp_path, backup_root=tmp_path)
    store = TaskProfileStore(profile_path)
    messages: list[tuple[str, str, dict]] = []
    calls: list[tuple[str, ...]] = []

    async def scenario():
        async def sender(message_type: str, request_id: str, payload: dict):
            messages.append((message_type, request_id, payload))

        runner = AgentTaskRunner(AgentConfig(data_dir=tmp_path), store, sender)

        async def fake_run(argv, *, timeout, cancel_event):
            calls.append(tuple(argv))
            return responses.pop(0)

        monkeypatch.setattr("agent.task_runner.platform.system", lambda: system_name)
        monkeypatch.setattr(runner, "_run_argv", fake_run)
        result = await runner._restart_service(
            profile,
            asyncio.Event(),
            uuid.uuid4().hex,
        )
        assert result["after"] in {"active", "running"}

    asyncio.run(scenario())
    assert calls == expected
