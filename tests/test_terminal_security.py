from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
import pytest


def _login(client, username="admin", password="test-bootstrap-password"):
    client.cookies.clear()
    response = client.post("/api/v2/auth/login", json={
        "username": username,
        "password": password,
    })
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": client.cookies.get("wcm_csrf")}


def _step_up(client, csrf, password="test-bootstrap-password"):
    response = client.post(
        "/api/v2/auth/step-up",
        headers=csrf,
        json={"password": password},
    )
    assert response.status_code == 200, response.text
    return response


def test_two_roles_step_up_and_permission_snapshot(client):
    csrf = _login(client)
    me = client.get("/api/v2/auth/me").json()
    assert me["role"] == "admin"
    assert "tasks.low" in me["permissions"]
    assert "terminal.admin" not in me["permissions"]
    assert client.put("/api/v2/settings/", headers=csrf, json={}).status_code == 403
    assert client.post(
        "/api/v2/auth/users",
        headers=csrf,
        json={"username": "before-step-up", "password": "long-enough-password", "role": "user"},
    ).status_code == 403

    assert client.post(
        "/api/v2/auth/step-up",
        headers=csrf,
        json={"password": "incorrect"},
    ).status_code == 403
    _step_up(client, csrf)
    elevated = client.get("/api/v2/auth/me").json()
    assert "terminal.admin" in elevated["permissions"]
    assert "settings.write" in elevated["permissions"]
    assert "settings.manage" not in elevated["permissions"]
    assert elevated["step_up_expires_at"]
    assert client.put("/api/v2/settings/", headers=csrf, json={}).status_code == 200

    created = client.post(
        "/api/v2/auth/users",
        headers=csrf,
        json={"username": f"user-{uuid.uuid4().hex[:8]}", "password": "long-enough-password", "role": "user"},
    )
    assert created.status_code == 201
    assert created.json()["role"] == "user"
    rejected = client.post(
        "/api/v2/auth/users",
        headers=csrf,
        json={"username": "legacy-role", "password": "long-enough-password", "role": "operator"},
    )
    assert rejected.status_code == 422

    assert client.delete("/api/v2/auth/step-up", headers=csrf).status_code == 204
    assert "terminal.admin" not in client.get("/api/v2/auth/me").json()["permissions"]


def test_last_enabled_admin_cannot_be_demoted_or_disabled(client):
    csrf = _login(client)
    _step_up(client, csrf)
    current = client.get("/api/v2/auth/me").json()

    demote = client.patch(
        f"/api/v2/auth/users/{current['id']}",
        headers=csrf,
        json={"role": "user"},
    )
    assert demote.status_code == 409
    assert "至少一个" in demote.json()["detail"]

    disable = client.patch(
        f"/api/v2/auth/users/{current['id']}",
        headers=csrf,
        json={"disabled": True},
    )
    assert disable.status_code == 409


def test_schema_v8_contains_two_role_model_terminal_and_update_metadata(client):
    import sqlite3

    from config import settings

    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        tables = {
            row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        user_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(users)")
        }
    assert version == 8
    assert {"disabled", "token_version", "role"}.issubset(user_columns)
    assert {
        "broker_credentials",
        "terminal_tickets",
        "terminal_sessions",
        "update_deployments",
        "update_deployment_targets",
        "update_attempts",
    }.issubset(tables)


def test_schema_v8_migrates_legacy_roles_invalidates_sessions_and_creates_backup(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = r"""
import asyncio
import sqlite3
from pathlib import Path

from sqlalchemy import text

import models  # noqa: F401
from database import Base, engine, init_db


async def prepare():
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(text(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
        ))
        for version in range(1, 7):
            await connection.execute(
                text("INSERT INTO schema_migrations VALUES (:version, CURRENT_TIMESTAMP)"),
                {"version": version},
            )
        for index, role in enumerate(("admin", "operator", "viewer", "unexpected")):
            await connection.execute(text(
                "INSERT INTO users "
                "(id, username, password, role, disabled, token_version, created) "
                "VALUES (:id, :username, 'hash', :role, 0, :version, CURRENT_TIMESTAMP)"
            ), {
                "id": f"user-{index}",
                "username": f"legacy-{index}",
                "role": role,
                "version": index + 2,
            })
        await connection.execute(text(
            "INSERT INTO update_packages "
            "(id, version, component, validation_status, created) "
            "VALUES ('legacy-package', '3.1.0', 'agent', 'verified', CURRENT_TIMESTAMP)"
        ))
    await engine.dispose()


asyncio.run(prepare())
asyncio.run(init_db())

data_dir = Path(__import__("os").environ["WCM_DATA_DIR"])
with sqlite3.connect(data_dir / "cluster.db") as connection:
    rows = connection.execute(
        "SELECT role, token_version FROM users ORDER BY username"
    ).fetchall()
    version = connection.execute(
        "SELECT MAX(version) FROM schema_migrations"
    ).fetchone()[0]
    legacy_status = connection.execute(
        "SELECT validation_status FROM update_packages WHERE id='legacy-package'"
    ).fetchone()[0]
assert rows == [("admin", 3), ("user", 4), ("user", 5), ("user", 6)], rows
assert version == 8
assert legacy_status == "legacy_untrusted"
assert len(list((data_dir / "backups").glob("cluster_pre_v8_*.db"))) == 1
"""
    env = os.environ.copy()
    env["WCM_DATA_DIR"] = str(tmp_path)
    env["PYTHONPATH"] = str(repo_root / "server")
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_terminal_crypto_rejects_tamper_replay_order_and_cross_session():
    from core.terminal_crypto import (
        TerminalCipher,
        TerminalProtocolError,
        new_ephemeral_key,
    )
    from agent.terminal_crypto import TerminalCipher as AgentCipher
    from agent.terminal_crypto import new_ephemeral_key as new_agent_key

    server_private, server_public = new_ephemeral_key()
    target_private, target_public = new_agent_key()
    server = TerminalCipher.server(
        server_private,
        target_public,
        session_id="session-a",
        node_id="node-a",
        mode="admin",
    )
    target = AgentCipher.target(
        target_private,
        server_public,
        session_id="session-a",
        node_id="node-a",
        mode="admin",
    )
    first = server.encrypt("terminal_data", {"data": "alpha"})
    second = server.encrypt("terminal_data", {"data": "beta"})

    with pytest.raises(Exception):
        target.decrypt(second)
    assert target.decrypt(first)[1]["data"] == "alpha"
    assert target.decrypt(second)[1]["data"] == "beta"
    with pytest.raises(Exception):
        target.decrypt(second)

    target_private2, target_public2 = new_agent_key()
    server_private2, server_public2 = new_ephemeral_key()
    wrong_session = AgentCipher.target(
        target_private2,
        server_public2,
        session_id="session-b",
        node_id="node-a",
        mode="admin",
    )
    with pytest.raises(Exception):
        wrong_session.decrypt(first)

    tamper_server_private, tamper_server_public = new_ephemeral_key()
    tamper_target_private, tamper_target_public = new_agent_key()
    tamper_server = TerminalCipher.server(
        tamper_server_private,
        tamper_target_public,
        session_id="session-c",
        node_id="node-a",
        mode="low",
    )
    tamper_target = AgentCipher.target(
        tamper_target_private,
        tamper_server_public,
        session_id="session-c",
        node_id="node-a",
        mode="low",
    )
    frame = tamper_server.encrypt("terminal_data", {"data": "canary"})
    ciphertext = bytearray(base64.b64decode(frame["ciphertext"]))
    ciphertext[-1] ^= 1
    frame["ciphertext"] = base64.b64encode(ciphertext).decode()
    with pytest.raises(Exception):
        tamper_target.decrypt(frame)
    tamper_server.destroy()
    with pytest.raises(TerminalProtocolError):
        tamper_server.encrypt("terminal_data", {})


def test_v2_terminal_profile_is_fixed_and_low_runner_streams_encrypted_output(tmp_path):
    from agent.task_profiles import TaskProfileStore
    from agent.terminal_runner import AgentTerminalManager
    from core.terminal_crypto import TerminalCipher, new_ephemeral_key

    profile_path = tmp_path / "task_profiles.json"
    profile_path.write_text(json.dumps({
        "version": 2,
        "log_profiles": {},
        "backup_profiles": {},
        "service_profiles": {},
        "command_profiles": {},
        "terminal_profiles": {
            "identity": {
                "argv": [sys.executable, "-c", "print('encrypted-canary')"],
                "timeout": 10,
                "output_limit": 65536,
            },
        },
    }), encoding="utf-8")
    store = TaskProfileStore(profile_path)
    assert store.capabilities()["terminal_protocol"] == 3
    sent: list[dict] = []

    async def send(message):
        sent.append(message)
        return True

    async def scenario():
        manager = AgentTerminalManager(
            lambda: "node-a",
            store,
            send,
            enabled=True,
        )
        server_private, server_public = new_ephemeral_key()
        await manager.handle({
            "type": "terminal_key_exchange",
            "version": 3,
            "session_id": "session-a",
            "node_id": "node-a",
            "mode": "low",
            "public_key": server_public,
        })
        target_key = sent.pop(0)
        server_cipher = TerminalCipher.server(
            server_private,
            target_key["public_key"],
            session_id="session-a",
            node_id="node-a",
            mode="low",
        )
        await manager.handle(server_cipher.encrypt("terminal_open", {
            "profile": "identity",
            "rows": 24,
            "cols": 80,
        }))
        for _ in range(100):
            if any(frame.get("type") == "terminal_exit" for frame in sent):
                break
            await asyncio.sleep(0.02)
        plaintext = []
        for frame in sent:
            frame_type, payload = server_cipher.decrypt(frame)
            if frame_type == "terminal_data":
                plaintext.append(base64.b64decode(payload["data"]).decode())
        assert "encrypted-canary" in "".join(plaintext)
        await manager.shutdown()

    asyncio.run(scenario())


def test_low_terminal_output_limit_terminates_process_immediately(tmp_path):
    from agent.task_profiles import TaskProfileStore
    from agent.terminal_runner import AgentTerminalManager
    from core.terminal_crypto import TerminalCipher, new_ephemeral_key

    profile_path = tmp_path / "task_profiles.json"
    profile_path.write_text(json.dumps({
        "version": 2,
        "log_profiles": {},
        "backup_profiles": {},
        "service_profiles": {},
        "command_profiles": {},
        "terminal_profiles": {
            "bounded": {
                "argv": [
                    sys.executable,
                    "-c",
                    "import sys,time;sys.stdout.write('x'*4096);sys.stdout.flush();time.sleep(20)",
                ],
                "timeout": 20,
                "output_limit": 1024,
            },
        },
    }), encoding="utf-8")
    store = TaskProfileStore(profile_path)
    sent: list[dict] = []

    async def send(message):
        sent.append(message)
        return True

    async def scenario():
        manager = AgentTerminalManager(lambda: "node-a", store, send, enabled=True)
        server_private, server_public = new_ephemeral_key()
        await manager.handle({
            "type": "terminal_key_exchange",
            "version": 3,
            "session_id": "bounded-session",
            "node_id": "node-a",
            "mode": "low",
            "public_key": server_public,
        })
        target_key = sent.pop(0)
        cipher = TerminalCipher.server(
            server_private,
            target_key["public_key"],
            session_id="bounded-session",
            node_id="node-a",
            mode="low",
        )
        started = time.monotonic()
        await manager.handle(cipher.encrypt("terminal_open", {
            "profile": "bounded",
            "rows": 24,
            "cols": 80,
        }))
        while manager.sessions and time.monotonic() - started < 5:
            await asyncio.sleep(0.02)
        assert not manager.sessions
        assert time.monotonic() - started < 5
        plaintext_frames = [cipher.decrypt(frame) for frame in sent]
        assert any(
            frame_type == "terminal_error"
            and payload["message"] == "terminal output limit exceeded"
            for frame_type, payload in plaintext_frames
        )
        await manager.shutdown()

    asyncio.run(scenario())


def test_broker_rejects_any_client_supplied_shell_parameters(monkeypatch):
    from broker.main import BrokerConfig, BrokerRuntime
    from core.terminal_crypto import TerminalCipher, new_ephemeral_key

    spawned = False

    async def forbidden_spawn(*_args):
        nonlocal spawned
        spawned = True
        raise AssertionError("privileged shell must not start")

    monkeypatch.setattr("broker.main.spawn_pty", forbidden_spawn)

    class FakeSocket:
        def __init__(self):
            self.messages = []

        async def send(self, raw):
            self.messages.append(json.loads(raw))

    async def scenario():
        runtime = BrokerRuntime(BrokerConfig(
            server_url="ws://localhost/ws/broker",
            node_id="node-a",
            token="opaque-token",
        ))
        runtime.ws = FakeSocket()
        server_private, server_public = new_ephemeral_key()
        await runtime.handle({
            "type": "terminal_key_exchange",
            "version": 3,
            "session_id": "admin-session",
            "node_id": "node-a",
            "mode": "admin",
            "public_key": server_public,
        })
        target_key = runtime.ws.messages.pop()
        cipher = TerminalCipher.server(
            server_private,
            target_key["public_key"],
            session_id="admin-session",
            node_id="node-a",
            mode="admin",
        )
        await runtime.handle(cipher.encrypt("terminal_open", {
            "rows": 24,
            "cols": 80,
            "command": "whoami",
        }))
        assert not runtime.sessions
        assert not spawned

    asyncio.run(scenario())


def test_one_time_terminal_ticket_is_sid_bound(client, monkeypatch):
    from api.terminal import _consume_ticket
    from config import settings
    from core.connection_manager import FrontendPrincipal, manager
    from core.security import decode_token

    csrf = _login(client)
    _step_up(client, csrf)
    enrollment = client.post(
        "/api/v2/agent-enrollment-tokens",
        headers=csrf,
        json={"label": "terminal-host"},
    )
    enrolled = client.post(
        "/api/v2/agents/enroll",
        headers={"Authorization": f"Bearer {enrollment.json()['token']}"},
        json={
            "agent_id": uuid.uuid4().hex,
            "ip": "10.77.0.1",
            "hostname": "terminal-host",
            "os": "Linux",
            "platform": "linux",
            "version": "4.0.0",
            "capabilities": {},
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    node_id = enrolled.json()["node_id"]

    class FakeBroker:
        async def send_json(self, _message):
            return None

        async def close(self, **_kwargs):
            return None

    monkeypatch.setattr(settings, "ENABLE_PRIVILEGED_TERMINAL", True)
    manager._brokers[node_id] = FakeBroker()
    response = client.post("/api/v2/terminal/tickets", headers=csrf, json={
        "node_id": node_id,
        "mode": "admin",
    })
    assert response.status_code == 201, response.text
    raw_ticket = response.json()["ticket"]
    access = decode_token(client.cookies.get("wcm_access"), expected_type="access")
    principal = FrontendPrincipal(
        access["sub"],
        "admin",
        int(access["ver"]),
        float(access["exp"]),
        str(access["sid"]),
    )
    wrong = FrontendPrincipal(
        principal.user_id,
        principal.role,
        principal.token_version,
        principal.expires_at,
        "wrong-sid",
    )
    assert asyncio.run(_consume_ticket(raw_ticket, wrong)) is None
    assert asyncio.run(_consume_ticket(raw_ticket, principal)) is not None
    assert asyncio.run(_consume_ticket(raw_ticket, principal)) is None
    manager._brokers.pop(node_id, None)


def test_low_terminal_websocket_routes_only_encrypted_agent_frames(
    client,
    monkeypatch,
):
    import sqlite3

    from agent.terminal_crypto import TerminalCipher as AgentCipher
    from agent.terminal_crypto import new_ephemeral_key as new_agent_key
    from config import settings

    csrf = _login(client)
    _step_up(client, csrf)
    issued = client.post(
        "/api/v2/agent-enrollment-tokens",
        headers=csrf,
        json={"label": "terminal-e2e"},
    )
    enrolled = client.post(
        "/api/v2/agents/enroll",
        headers={"Authorization": f"Bearer {issued.json()['token']}"},
        json={
            "agent_id": uuid.uuid4().hex,
            "ip": "10.77.0.2",
            "hostname": "terminal-e2e",
            "os": "Linux",
            "platform": "linux",
            "version": "4.0.0",
            "capabilities": {},
        },
    )
    node_id = enrolled.json()["node_id"]
    monkeypatch.setattr(settings, "ENABLE_LOW_TERMINAL", True)
    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        connection.execute(
            "UPDATE nodes SET capabilities=? WHERE id=?",
            (json.dumps({
                "terminal_protocol": 3,
                "terminal_profiles": ["identity"],
            }), node_id),
        )
        connection.commit()

    from services.terminal_service import terminal_service

    class EncryptedFakeAgent:
        def __init__(self):
            self.cipher = None
            self.closed = False

        async def send_json(self, message):
            if message["type"] == "terminal_key_exchange":
                target_private, target_public = new_agent_key()
                self.cipher = AgentCipher.target(
                    target_private,
                    message["public_key"],
                    session_id=message["session_id"],
                    node_id=node_id,
                    mode="low",
                )
                await terminal_service.handle_target_message(node_id, {
                    "type": "terminal_key_exchange",
                    "version": 3,
                    "session_id": message["session_id"],
                    "node_id": node_id,
                    "mode": "low",
                    "public_key": target_public,
                }, target_kind="agent")
                return
            assert self.cipher is not None
            frame_type, payload = self.cipher.decrypt(message)
            if frame_type == "terminal_open":
                assert frame_type == "terminal_open"
                assert payload["profile"] == "identity"
                await terminal_service.handle_target_message(
                    node_id,
                    self.cipher.encrypt("terminal_data", {
                    "data": base64.b64encode(b"encrypted-ws-canary\r\n").decode(),
                    }),
                    target_kind="agent",
                )
                await terminal_service.handle_target_message(
                    node_id,
                    self.cipher.encrypt("terminal_exit", {
                        "exit_code": 0,
                        "reason": "completed",
                    }),
                    target_kind="agent",
                )
            elif frame_type == "terminal_close":
                self.closed = True

        async def close(self, **_kwargs):
            return None

    fake_agent = EncryptedFakeAgent()
    from core.connection_manager import manager
    manager._agents[node_id] = fake_agent
    ticket_response = client.post(
        "/api/v2/terminal/tickets",
        headers=csrf,
        json={"node_id": node_id, "mode": "low", "profile": "identity"},
    )
    assert ticket_response.status_code == 201, ticket_response.text
    ticket = ticket_response.json()["ticket"]
    messages = []
    with client.websocket_connect("/ws/terminal") as browser:
        browser.send_json({"type": "auth", "ticket": ticket})
        for _ in range(4):
            message = browser.receive_json()
            messages.append(message)
            if message.get("type") == "exit":
                break
    assert any(item.get("type") == "ready" for item in messages)
    assert any(
        item.get("type") == "data" and "encrypted-ws-canary" in item.get("data", "")
        for item in messages
    )
    with sqlite3.connect(settings.DATA_DIR / "cluster.db") as connection:
        serialized = "\n".join(
            str(row) for row in connection.execute(
                "SELECT * FROM terminal_sessions"
            ).fetchall()
        )
    assert "encrypted-ws-canary" not in serialized
    assert fake_agent.closed
    manager._agents.pop(node_id, None)


def test_broker_rejects_non_loopback_plain_websocket(monkeypatch):
    from broker.main import BrokerConfig

    class Args:
        server = "ws://10.10.10.10/ws/broker"
        node_id = str(uuid.uuid4())
        ca_cert = ""

    monkeypatch.setenv("WCM_BROKER_TOKEN", "test-broker-token")
    with pytest.raises(ValueError):
        BrokerConfig.from_args(Args())

    Args.server = "ws://attacker.invalid/ws/broker?next=localhost"
    with pytest.raises(ValueError):
        BrokerConfig.from_args(Args())
