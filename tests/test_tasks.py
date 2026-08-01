from __future__ import annotations

import asyncio
import time
import uuid
import sqlite3
from datetime import datetime, timedelta, timezone


def _login(client, username: str, password: str) -> dict[str, str]:
    client.cookies.clear()
    response = client.post("/api/v2/auth/login", json={
        "username": username,
        "password": password,
    })
    assert response.status_code == 200, response.text
    csrf = {"X-CSRF-Token": client.cookies.get("wcm_csrf")}
    if username == "admin":
        assert client.post(
            "/api/v2/auth/step-up",
            headers=csrf,
            json={"password": password},
        ).status_code == 200
    return csrf


def _create_user(client, csrf, role: str):
    suffix = uuid.uuid4().hex[:8]
    username = f"task-{role}-{suffix}"
    password = f"task-{role}-{suffix}-password"
    response = client.post("/api/v2/auth/users", headers=csrf, json={
        "username": username,
        "password": password,
        "role": role,
    })
    assert response.status_code == 201, response.text
    return username, password


def _enroll(client, csrf, hostname: str, log_profiles: list[str]):
    issued = client.post(
        "/api/v2/agent-enrollment-tokens",
        headers=csrf,
        json={"label": hostname},
    )
    assert issued.status_code == 201, issued.text
    token = issued.json()["token"]
    enrolled = client.post(
        "/api/v2/agents/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "agent_id": uuid.uuid4().hex,
            "ip": f"10.30.{int(uuid.uuid4().hex[:2], 16)}.{int(uuid.uuid4().hex[2:4], 16)}",
            "hostname": hostname,
            "os": "Linux test",
            "platform": "linux",
            "version": "3.1.0",
            "capabilities": {
                "task_protocol": 2,
                "task_profiles": {
                    "clean_logs": log_profiles,
                    "backup_files": ["application"],
                    "restart_service": ["web"],
                    "batch_command": ["diagnostic"],
                },
            },
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    return enrolled.json()["node_id"]


def _wait_for_status(client, task_id: str, expected: set[str]):
    detail = None
    for _ in range(30):
        detail = client.get(f"/api/v2/tasks/{task_id}")
        assert detail.status_code == 200, detail.text
        if detail.json()["status"] in expected:
            return detail.json()
        time.sleep(0.05)
    raise AssertionError(f"task did not reach {expected}: {detail.text if detail else ''}")


def test_schema_v8_preserves_task_columns(client):
    from config import settings

    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations"
        ).fetchone()[0]
        task_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(tasks)").fetchall()
        }
        subtask_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(subtasks)").fetchall()
        }
    assert version == 8
    assert {"started", "updated"}.issubset(task_columns)
    assert {
        "execution_id", "attempts", "progress", "message", "error",
    }.issubset(subtask_columns)


def test_schema_v8_backup_uses_consistent_sqlite_snapshot(tmp_path, monkeypatch):
    from config import settings
    from database import _backup_before_migration

    db_path = tmp_path / "cluster.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (5, '2026-07-25 00:00:00')"
        )
        connection.execute("CREATE TABLE evidence(value TEXT NOT NULL)")
        connection.execute("INSERT INTO evidence VALUES ('committed-wal-data')")
        connection.commit()
        monkeypatch.setattr(settings, "DATA_DIR", tmp_path)
        _backup_before_migration()

    backups = list((tmp_path / "backups").glob("cluster_pre_v8_*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as backup:
        assert backup.execute("SELECT value FROM evidence").fetchone()[0] == "committed-wal-data"


def test_terminal_task_history_is_removed_after_90_days(client):
    from config import settings
    from services.task_service import task_service

    old_id = str(uuid.uuid4())
    current_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        for task_id, finished in (
            (old_id, now - timedelta(days=91)),
            (current_id, now - timedelta(days=1)),
        ):
            connection.execute(
                "INSERT INTO tasks "
                "(id, type, params, status, created, updated, finished) "
                "VALUES (?, 'health_check', '{}', 'completed', ?, ?, ?)",
                (
                    task_id,
                    finished.replace(tzinfo=None).isoformat(sep=" "),
                    finished.replace(tzinfo=None).isoformat(sep=" "),
                    finished.replace(tzinfo=None).isoformat(sep=" "),
                ),
            )
        connection.commit()

    assert asyncio.run(task_service.cleanup_history()) >= 1
    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        remaining = {
            row[0] for row in connection.execute(
                "SELECT id FROM tasks WHERE id IN (?, ?)",
                (old_id, current_id),
            ).fetchall()
        }
    assert old_id not in remaining
    assert current_id in remaining


def test_task_targets_roles_pause_cancel_and_selected_retry(client):
    admin_csrf = _login(client, "admin", "test-bootstrap-password")
    operator_name, operator_password = _create_user(client, admin_csrf, "user")
    viewer_name, viewer_password = _create_user(client, admin_csrf, "user")
    node_one = _enroll(client, admin_csrf, "task-node-one", ["shared", "node-one"])
    node_two = _enroll(client, admin_csrf, "task-node-two", ["shared", "node-two"])

    group = client.post(
        "/api/v2/nodes/groups/",
        headers=admin_csrf,
        json={"name": f"task-group-{uuid.uuid4().hex[:8]}"},
    )
    assert group.status_code == 201, group.text
    group_id = group.json()["id"]
    assert client.post(
        f"/api/v2/nodes/groups/{group_id}/nodes/{node_one}", headers=admin_csrf,
    ).status_code == 204
    assert client.post(
        f"/api/v2/nodes/groups/{group_id}/nodes/{node_two}", headers=admin_csrf,
    ).status_code == 204

    resolved = client.post("/api/v2/tasks/targets/resolve", headers=admin_csrf, json={
        "target_node_ids": [node_one],
        "target_group_ids": [group_id],
        "all_online": False,
    })
    assert resolved.status_code == 200, resolved.text
    assert {item["id"] for item in resolved.json()["nodes"]} == {node_one, node_two}
    assert resolved.json()["common_profiles"]["clean_logs"] == ["shared"]

    too_many = client.post("/api/v2/tasks/targets/resolve", headers=admin_csrf, json={
        "target_node_ids": [str(index) for index in range(33)],
        "target_group_ids": [],
        "all_online": False,
    })
    assert too_many.status_code == 422

    operator_csrf = _login(client, operator_name, operator_password)
    forbidden = client.post("/api/v2/tasks/", headers=operator_csrf, json={
        "type": "restart_service",
        "title": "operator cannot restart",
        "params": {"profile": "web"},
        "target_node_ids": [node_one],
        "target_group_ids": [],
        "all_online": False,
    })
    assert forbidden.status_code == 403

    mismatched_profile = client.post("/api/v2/tasks/", headers=operator_csrf, json={
        "type": "clean_logs",
        "params": {
            "profile": "node-one",
            "older_than_days": 7,
            "dry_run": True,
        },
        "target_node_ids": [node_one, node_two],
        "target_group_ids": [],
        "all_online": False,
    })
    assert mismatched_profile.status_code == 422

    created = client.post("/api/v2/tasks/", headers=operator_csrf, json={
        "type": "clean_logs",
        "title": "shared preview",
        "params": {
            "profile": "shared",
            "older_than_days": 7,
            "dry_run": True,
        },
        "target_node_ids": [node_one],
        "target_group_ids": [group_id],
        "all_online": False,
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    detail = _wait_for_status(client, task_id, {"paused"})
    assert len(detail["subtasks"]) == 2
    assert {item["status"] for item in detail["subtasks"]} == {"paused"}
    assert all(item["error"] == "Agent offline" for item in detail["subtasks"])

    retry_invalid = client.post(
        f"/api/v2/tasks/{task_id}/retry",
        headers=operator_csrf,
        json={"target_node_ids": [uuid.uuid4().hex]},
    )
    assert retry_invalid.status_code == 422
    retry_one = client.post(
        f"/api/v2/tasks/{task_id}/retry",
        headers=operator_csrf,
        json={"target_node_ids": [node_one]},
    )
    assert retry_one.status_code == 200, retry_one.text
    detail = _wait_for_status(client, task_id, {"paused"})
    assert {
        item["node_id"]: item["status"] for item in detail["subtasks"]
    } == {node_one: "paused", node_two: "paused"}

    cancelled = client.post(
        f"/api/v2/tasks/{task_id}/cancel", headers=operator_csrf,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["status"] == "cancelled"

    formal_without_completed_preview = client.post("/api/v2/tasks/", headers=operator_csrf, json={
        "type": "clean_logs",
        "params": {
            "profile": "shared",
            "older_than_days": 7,
            "dry_run": False,
            "preview_task_id": task_id,
        },
        "target_node_ids": [node_one, node_two],
        "target_group_ids": [],
        "all_online": False,
    })
    assert formal_without_completed_preview.status_code == 403

    viewer_csrf = _login(client, viewer_name, viewer_password)
    assert client.get("/api/v2/tasks/").status_code == 200
    assert client.post("/api/v2/tasks/targets/resolve", headers=viewer_csrf, json={
        "target_node_ids": [node_one],
        "target_group_ids": [],
        "all_online": False,
    }).status_code == 200


def test_cancel_requires_agent_ack_and_timeout_is_not_forged(client, monkeypatch):
    from config import settings
    from core.connection_manager import manager

    admin_csrf = _login(client, "admin", "test-bootstrap-password")
    node_id = _enroll(client, admin_csrf, "task-cancel-agent", [])

    class FakeAgent:
        def __init__(self, acknowledge_cancel: bool):
            self.acknowledge_cancel = acknowledge_cancel

        async def send_json(self, message):
            if message["type"] == "task":
                manager.resolve_request(
                    node_id,
                    message["request_id"],
                    {"accepted": True},
                )
            elif message["type"] == "task_cancel" and self.acknowledge_cancel:
                manager.resolve_request(
                    node_id,
                    message["request_id"],
                    {"cancelled": True},
                )

    def create_running(title: str):
        created = client.post("/api/v2/tasks/", headers=admin_csrf, json={
            "type": "health_check",
            "title": title,
            "params": {},
            "target_node_ids": [node_id],
            "target_group_ids": [],
            "all_online": False,
        })
        assert created.status_code == 201, created.text
        return created.json()["id"]

    manager._agents[node_id] = FakeAgent(True)
    try:
        task_id = create_running("cancel-ack")
        _wait_for_status(client, task_id, {"running"})
        cancelled = client.post(f"/api/v2/tasks/{task_id}/cancel", headers=admin_csrf)
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "cancelled"

        manager._agents[node_id] = FakeAgent(False)
        timeout_task = create_running("cancel-timeout")
        _wait_for_status(client, timeout_task, {"running"})
        monkeypatch.setattr(settings, "TASK_CANCEL_TIMEOUT_SECONDS", 0.01)
        timed_out = client.post(
            f"/api/v2/tasks/{timeout_task}/cancel",
            headers=admin_csrf,
        )
        assert timed_out.status_code == 200, timed_out.text
        assert timed_out.json()["status"] == "cancel_requested"
        detail = client.get(f"/api/v2/tasks/{timeout_task}").json()
        assert detail["subtasks"][0]["status"] == "cancel_requested"
    finally:
        manager._agents.pop(node_id, None)


def test_fast_agent_result_cannot_be_overwritten_by_ack_transition(client):
    from core.connection_manager import manager
    from services.task_service import task_service

    admin_csrf = _login(client, "admin", "test-bootstrap-password")
    node_id = _enroll(client, admin_csrf, "task-fast-result-agent", [])

    class FastAgent:
        async def send_json(self, message):
            if message["type"] != "task":
                return
            manager.resolve_request(
                node_id,
                message["request_id"],
                {"accepted": True},
            )
            await task_service.handle_result(
                node_id,
                message["request_id"],
                {
                    "success": True,
                    "status": "completed",
                    "message": "fast",
                    "result": {"healthy": True},
                },
            )

    manager._agents[node_id] = FastAgent()
    try:
        created = client.post("/api/v2/tasks/", headers=admin_csrf, json={
            "type": "health_check",
            "params": {},
            "target_node_ids": [node_id],
            "target_group_ids": [],
            "all_online": False,
        })
        assert created.status_code == 201, created.text
        detail = _wait_for_status(client, created.json()["id"], {"completed"})
        assert detail["subtasks"][0]["status"] == "completed"
        assert detail["subtasks"][0]["result"] == {"healthy": True}
    finally:
        manager._agents.pop(node_id, None)
