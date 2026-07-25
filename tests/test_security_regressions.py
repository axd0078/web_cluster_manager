from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from datetime import datetime, timezone

import pytest
from starlette.websockets import WebSocketDisconnect


def _login(client, username: str, password: str) -> dict[str, str]:
    client.cookies.clear()
    response = client.post("/api/v2/auth/login", json={
        "username": username,
        "password": password,
    })
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("wcm_csrf")}


def _create_user(client, csrf: dict[str, str], role: str) -> tuple[str, str, str]:
    suffix = uuid.uuid4().hex[:8]
    username = f"{role}-{suffix}"
    password = f"pytest-{role}-{suffix}-password"
    response = client.post("/api/v2/auth/users", headers=csrf, json={
        "username": username,
        "password": password,
        "role": role,
    })
    assert response.status_code == 201, response.text
    return response.json()["id"], username, password


def test_unknown_username_still_performs_one_password_verification(client, monkeypatch):
    from api import auth
    from core.security import DUMMY_PASSWORD_HASH

    auth._failed_logins.clear()
    calls: list[tuple[str, str]] = []

    def fake_verify(plain: str, hashed: str) -> bool:
        calls.append((plain, hashed))
        return False

    monkeypatch.setattr(auth, "verify_password", fake_verify)
    response = client.post("/api/v2/auth/login", json={
        "username": f"missing-{uuid.uuid4().hex}",
        "password": "wrong-password",
    })
    assert response.status_code == 401
    assert calls == [("wrong-password", DUMMY_PASSWORD_HASH)]


def test_logout_closes_live_frontend_socket_and_rejects_old_cookie(client):
    csrf = _login(client, "admin", "test-bootstrap-password")
    old_access = client.cookies.get("wcm_access")
    with client.websocket_connect("/ws/frontend") as websocket:
        websocket.send_text("ping")
        assert websocket.receive_text() == "pong"
        assert client.post("/api/v2/auth/logout", headers=csrf).status_code == 204
        with pytest.raises(WebSocketDisconnect) as closed:
            websocket.receive_text()
        assert closed.value.code == 4001

    client.cookies.set("wcm_access", old_access)
    with pytest.raises(WebSocketDisconnect) as rejected:
        with client.websocket_connect("/ws/frontend"):
            pass
    assert rejected.value.code == 4001


def test_frontend_broadcasts_are_role_scoped_and_agent_replies_are_node_bound():
    from core.connection_manager import (
        ConnectionManager,
        FrontendPrincipal,
        PendingAgentRequest,
    )

    class FakeWebSocket:
        def __init__(self):
            self.accepted = False
            self.closed: tuple[int, str] | None = None
            self.messages: list[dict] = []

        async def accept(self):
            self.accepted = True

        async def close(self, code: int = 1000, reason: str = ""):
            self.closed = (code, reason)

        async def send_json(self, data: dict):
            self.messages.append(data)

    async def scenario():
        connection_manager = ConnectionManager()
        admin = FakeWebSocket()
        viewer = FakeWebSocket()
        expires = time.time() + 60
        await connection_manager.frontend_connect(
            admin,
            FrontendPrincipal("admin-id", "admin", 0, expires),
        )
        await connection_manager.frontend_connect(
            viewer,
            FrontendPrincipal("viewer-id", "viewer", 0, expires),
        )
        event = {"type": "privileged", "payload": {"secret": "admin-only"}}
        await connection_manager.broadcast_to_frontends(
            event, allowed_roles={"admin"},
        )
        assert admin.messages == [event]
        assert viewer.messages == []

        future = asyncio.get_running_loop().create_future()
        connection_manager._requests["request-1"] = PendingAgentRequest(
            node_id="trusted-node", future=future,
        )
        assert connection_manager.resolve_request(
            "spoofing-node", "request-1", {"success": True},
        ) is False
        assert future.done() is False
        assert connection_manager.resolve_request(
            "trusted-node", "request-1", {"success": True},
        ) is True
        assert await future == {"success": True}

    asyncio.run(scenario())


def test_agent_payload_cannot_override_trusted_event_identifiers():
    from api.ws import _task_result_event

    event = _task_result_event("trusted-node", "trusted-request", {
        "success": True,
        "node_id": "spoofed-node",
        "request_id": "spoofed-request",
        "stdout": "must-not-be-broadcast",
    })
    assert event == {
        "type": "task_result",
        "payload": {
            "success": True,
            "node_id": "trusted-node",
            "request_id": "trusted-request",
        },
    }


def test_operator_cannot_read_cancel_or_retry_another_operators_task(client):
    admin_csrf = _login(client, "admin", "test-bootstrap-password")
    owner_id, owner_name, owner_password = _create_user(client, admin_csrf, "operator")
    _, other_name, other_password = _create_user(client, admin_csrf, "operator")
    enrollment = client.post(
        "/api/v2/agent-enrollment-tokens",
        headers=admin_csrf,
        json={"label": "task-ownership-test"},
    )
    token = enrollment.json()["token"]
    enrolled = client.post(
        "/api/v2/agents/enroll",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "agent_id": uuid.uuid4().hex,
            "ip": f"10.20.0.{int(uuid.uuid4().hex[:2], 16) or 1}",
            "hostname": "task-owner-agent",
            "os": "Linux test",
            "platform": "linux",
            "version": "3.1.0",
            "capabilities": {
                "task_protocol": 2,
                "task_profiles": {
                    "clean_logs": [],
                    "backup_files": [],
                    "restart_service": [],
                    "batch_command": [],
                },
            },
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    node_id = enrolled.json()["node_id"]

    owner_csrf = _login(client, owner_name, owner_password)
    created = client.post("/api/v2/tasks/", headers=owner_csrf, json={
        "type": "health_check",
        "title": "owner-only-task",
        "params": {},
        "target_node_ids": [node_id],
    })
    assert created.status_code == 201, created.text
    task_id = created.json()["id"]
    assert created.json()["created_by"] == owner_id

    other_csrf = _login(client, other_name, other_password)
    assert client.get(f"/api/v2/tasks/{task_id}").status_code == 404
    assert client.post(
        f"/api/v2/tasks/{task_id}/cancel", headers=other_csrf,
    ).status_code == 404
    assert client.post(
        f"/api/v2/tasks/{task_id}/retry", headers=other_csrf,
    ).status_code == 404
    assert task_id not in {item["id"] for item in client.get("/api/v2/tasks/").json()}

    _login(client, "admin", "test-bootstrap-password")
    assert client.get(f"/api/v2/tasks/{task_id}").json()["status"] in {"queued", "paused"}


def test_viewer_cannot_read_another_users_file_transfer_history(client):
    from config import settings

    admin_csrf = _login(client, "admin", "test-bootstrap-password")
    admin_id = client.get("/api/v2/auth/me").json()["id"]
    transfer_id = str(uuid.uuid4())
    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        connection.execute(
            "INSERT INTO file_transfers "
            "(id, filename, size, source, targets, status, sha256, dest_path, "
            "overwrite, created_by, created, started, finished) "
            "VALUES (?, ?, ?, NULL, ?, ?, ?, ?, 0, ?, ?, NULL, NULL)",
            (
                transfer_id,
                "admin-only.bin",
                1,
                "[]",
                "completed",
                "0" * 64,
                "private/admin-only.bin",
                admin_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    _, viewer_name, viewer_password = _create_user(client, admin_csrf, "viewer")
    _login(client, viewer_name, viewer_password)
    transfers = client.get("/api/v2/files/transfers")
    assert transfers.status_code == 200
    assert transfer_id not in {item["id"] for item in transfers.json()}
    assert client.get(f"/api/v2/files/transfers/{transfer_id}").status_code == 404
