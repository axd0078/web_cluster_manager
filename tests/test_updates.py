from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import platform
import sys
import uuid
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _login(client, *, elevate: bool = True) -> dict[str, str]:
    client.cookies.clear()
    response = client.post("/api/v2/auth/login", json={
        "username": "admin", "password": "test-bootstrap-password",
    })
    assert response.status_code == 200, response.text
    csrf = {"X-CSRF-Token": client.cookies.get("wcm_csrf")}
    if elevate:
        response = client.post(
            "/api/v2/auth/step-up",
            headers=csrf,
            json={"password": "test-bootstrap-password"},
        )
        assert response.status_code == 200, response.text
    return csrf


def _runtime() -> tuple[str, str, str]:
    os_family = platform.system().lower()
    machine = platform.machine().lower()
    arch = "x86_64" if machine in {"amd64", "x86_64"} else "aarch64"
    return os_family, arch, f"cp{sys.version_info.major}{sys.version_info.minor}"


def _package(
    tmp_path: Path,
    private_key: Ed25519PrivateKey,
    *,
    signature=None,
    extra_payload: dict[str, bytes] | None = None,
) -> Path:
    from core.update_package import canonical_manifest

    os_family, arch, python_abi = _runtime()
    files = {
        "main.py": b"print('trusted update')\n",
        "requirements.lock": b"",
        **(extra_payload or {}),
    }
    manifest = {
        "schema_version": 1,
        "component": "agent",
        "version": "4.1.0",
        "release_id": f"pytest-{uuid.uuid4().hex}",
        "created_at": "2026-08-01T00:00:00Z",
        "key_id": "pytest-update",
        "target": {"os": os_family, "arch": arch, "python_abi": python_abi},
        "min_updater_version": "2.0.0",
        "entrypoint": "main.py",
        "files": [
            {
                "path": name,
                "size": len(content),
                "sha256": hashlib.sha256(content).hexdigest(),
            }
            for name, content in files.items()
        ],
    }
    signed = private_key.sign(canonical_manifest(manifest)) if signature is None else signature
    path = tmp_path / f"{manifest['release_id']}.wcmupd"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("manifest.sig", base64.b64encode(signed))
        for name, content in files.items():
            archive.writestr(f"payload/{name}", content)
    return path


def _trusted_key() -> tuple[Ed25519PrivateKey, Path]:
    from config import settings

    private_key = Ed25519PrivateKey.generate()
    settings.UPDATE_TRUSTED_KEYS_DIR.mkdir(parents=True, exist_ok=True)
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    key_path = settings.UPDATE_TRUSTED_KEYS_DIR / "pytest-update.pub"
    key_path.write_bytes(base64.b64encode(raw))
    return private_key, key_path


def _enroll_update_agent(client, csrf: dict[str, str], release_id: str = "old-release") -> str:
    issued = client.post(
        "/api/v2/agent-enrollment-tokens",
        headers=csrf,
        json={"label": "update-test"},
    )
    assert issued.status_code == 201, issued.text
    os_family, arch, python_abi = _runtime()
    enrolled = client.post(
        "/api/v2/agents/enroll",
        headers={"Authorization": f"Bearer {issued.json()['token']}"},
        json={
            "agent_id": uuid.uuid4().hex,
            "ip": f"10.77.{int(uuid.uuid4().hex[:2], 16)}.{int(uuid.uuid4().hex[2:4], 16)}",
            "hostname": "update-test-agent",
            "os": f"{os_family} test",
            "platform": os_family,
            "version": "3.1.0",
            "capabilities": {
                "update_protocol": 2,
                "updater_version": "2.0.0",
                "os_family": os_family,
                "architecture": arch,
                "python_abi": python_abi,
                "active_release_id": release_id,
                "active_version": "3.1.0",
            },
        },
    )
    assert enrolled.status_code == 201, enrolled.text
    return enrolled.json()["node_id"]


def test_signed_package_permissions_compatibility_and_canary_creation(
    client, tmp_path: Path, monkeypatch,
):
    from services.update_service import update_service

    csrf = _login(client)
    private_key, key_path = _trusted_key()
    package_path = _package(tmp_path, private_key)
    uploaded = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (package_path.name, package_path.read_bytes(), "application/zip")},
        params={"description": "pytest signed release"},
    )
    assert uploaded.status_code == 201, uploaded.text
    package = uploaded.json()
    assert package["validation_status"] == "verified"
    assert package["sha256"] == hashlib.sha256(package_path.read_bytes()).hexdigest()
    assert package["key_id"] == "pytest-update"

    node_id = _enroll_update_agent(client, csrf)
    resolved = client.post(
        "/api/v2/updates/targets/resolve",
        headers=csrf,
        json={
            "package_id": package["id"],
            "target_node_ids": [node_id, node_id],
            "target_group_ids": [],
            "all_online": False,
        },
    )
    assert resolved.status_code == 200, resolved.text
    resolved_nodes = resolved.json()["nodes"]
    assert len(resolved_nodes) == 1
    assert resolved_nodes[0]["id"] == node_id
    assert resolved_nodes[0]["compatible"] is True
    assert resolved_nodes[0]["incompatibility"] is None
    assert resolved_nodes[0]["reasons"] == []

    monkeypatch.setattr(update_service, "start", lambda *_args, **_kwargs: True)
    created = client.post(
        "/api/v2/updates/deployments",
        headers=csrf,
        json={
            "package_id": package["id"],
            "target_node_ids": [node_id],
            "target_group_ids": [],
            "all_online": False,
            "canary_node_ids": [node_id],
        },
    )
    assert created.status_code == 202, created.text
    deployment = created.json()
    assert deployment["status"] == "queued"
    assert deployment["canary_node_ids"] == [node_id]
    assert deployment["targets"][0]["is_canary"] is True
    assert deployment["targets"][0]["from_release_id"] == "old-release"

    assert client.post(
        "/api/v2/updates/push", headers=csrf,
        params={"package_id": package["id"], "target_node_ids": node_id},
    ).status_code == 410
    assert client.delete(
        f"/api/v2/updates/packages/{package['id']}", headers=csrf,
    ).status_code == 409
    key_path.unlink(missing_ok=True)


def test_update_package_rejects_bad_signature_and_requires_step_up(client, tmp_path: Path):
    csrf = _login(client, elevate=False)
    private_key, key_path = _trusted_key()
    package_path = _package(tmp_path, private_key)
    denied = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (package_path.name, package_path.read_bytes(), "application/zip")},
    )
    assert denied.status_code == 403

    csrf = _login(client)
    tampered = _package(tmp_path, private_key, signature=b"\x00" * 64)
    rejected = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (tampered.name, tampered.read_bytes(), "application/zip")},
    )
    assert rejected.status_code == 400
    assert "签名" in rejected.json()["detail"]

    oversized = _package(tmp_path, private_key, signature=b"x" * 2048)
    rejected = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (oversized.name, oversized.read_bytes(), "application/zip")},
    )
    assert rejected.status_code == 400
    assert "manifest.sig" in rejected.json()["detail"]
    key_path.unlink(missing_ok=True)


def test_update_target_rejects_incompatible_agent(client, tmp_path: Path):
    csrf = _login(client)
    private_key, key_path = _trusted_key()
    package_path = _package(tmp_path, private_key)
    uploaded = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (package_path.name, package_path.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 201, uploaded.text
    node_id = _enroll_update_agent(client, csrf, release_id=uploaded.json()["release_id"])
    resolved = client.post(
        "/api/v2/updates/targets/resolve",
        headers=csrf,
        json={
            "package_id": uploaded.json()["id"],
            "target_node_ids": [node_id],
            "target_group_ids": [],
            "all_online": False,
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["nodes"][0]["compatible"] is False
    assert "已运行" in resolved.json()["nodes"][0]["incompatibility"]
    creation = client.post(
        "/api/v2/updates/deployments",
        headers=csrf,
        json={
            "package_id": uploaded.json()["id"],
            "target_node_ids": [node_id],
            "target_group_ids": [],
            "all_online": False,
            "canary_node_ids": [node_id],
        },
    )
    assert creation.status_code == 422
    key_path.unlink(missing_ok=True)


def test_multi_node_chunked_canary_approval_and_health_completion(
    client, tmp_path: Path, monkeypatch,
):
    from config import settings
    from core.connection_manager import manager
    from services.update_service import update_service

    csrf = _login(client)
    private_key, key_path = _trusted_key()
    package_path = _package(
        tmp_path,
        private_key,
        extra_payload={"assets/random.bin": __import__("os").urandom(2_500_000)},
    )
    uploaded = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (package_path.name, package_path.read_bytes(), "application/zip")},
    )
    assert uploaded.status_code == 201, uploaded.text
    package = uploaded.json()
    node_one = _enroll_update_agent(client, csrf, "old-one")
    node_two = _enroll_update_agent(client, csrf, "old-two")
    empty_canary = client.post(
        "/api/v2/updates/deployments",
        headers=csrf,
        json={
            "package_id": package["id"],
            "target_node_ids": [node_one, node_two],
            "target_group_ids": [],
            "all_online": False,
            "canary_node_ids": [],
        },
    )
    assert empty_canary.status_code == 422, empty_canary.text
    all_canary = client.post(
        "/api/v2/updates/deployments",
        headers=csrf,
        json={
            "package_id": package["id"],
            "target_node_ids": [node_one, node_two],
            "target_group_ids": [],
            "all_online": False,
            "canary_node_ids": [node_one, node_two],
        },
    )
    assert all_canary.status_code == 422, all_canary.text
    assert "非 canary" in all_canary.json()["detail"]
    buffers: dict[str, bytearray] = {}
    max_chunk = 0

    async def fake_request_agent(node_id: str, message: dict, timeout: float = 0) -> dict:
        nonlocal max_chunk
        message_type = message["type"]
        payload = message["payload"]
        execution_id = payload["execution_id"]
        if message_type == "update_transfer_init":
            buffers.setdefault(execution_id, bytearray())
            return {"success": True, "next_offset": len(buffers[execution_id])}
        if message_type == "update_transfer_chunk":
            raw = base64.b64decode(payload["content_b64"], validate=True)
            max_chunk = max(max_chunk, len(raw))
            assert payload["offset"] == len(buffers[execution_id])
            assert hashlib.sha256(raw).hexdigest() == payload["chunk_sha256"]
            buffers[execution_id].extend(raw)
            return {"success": True, "next_offset": len(buffers[execution_id])}
        if message_type == "update_transfer_commit":
            assert hashlib.sha256(buffers[execution_id]).hexdigest() == payload["sha256"]
            return {"success": True}
        if message_type == "update_activate":
            async def confirm_health():
                await asyncio.sleep(0.05)
                await update_service.handle_health(node_id, {
                    "execution_id": execution_id,
                    "release_id": payload["release_id"],
                    "version": payload["version"],
                    "operation": "activate",
                })

            asyncio.create_task(confirm_health())
            return {"success": True, "accepted": True}
        raise AssertionError(f"unexpected Agent message: {message_type}")

    monkeypatch.setattr(manager, "request_agent", fake_request_agent)
    created = client.post(
        "/api/v2/updates/deployments",
        headers=csrf,
        json={
            "package_id": package["id"],
            "target_node_ids": [node_one, node_two],
            "target_group_ids": [],
            "all_online": False,
            "canary_node_ids": [node_one],
        },
    )
    assert created.status_code == 202, created.text
    deployment_id = created.json()["id"]

    detail = None
    for _ in range(100):
        response = client.get(f"/api/v2/updates/deployments/{deployment_id}")
        assert response.status_code == 200, response.text
        detail = response.json()
        if detail["status"] == "awaiting_approval":
            break
        __import__("time").sleep(0.05)
    assert detail and detail["status"] == "awaiting_approval"
    assert next(item for item in detail["targets"] if item["node_id"] == node_two)["status"] == "queued"

    approved = client.post(
        f"/api/v2/updates/deployments/{deployment_id}/approve",
        headers=csrf,
    )
    assert approved.status_code == 202, approved.text
    for _ in range(100):
        detail = client.get(f"/api/v2/updates/deployments/{deployment_id}").json()
        if detail["status"] == "completed":
            break
        __import__("time").sleep(0.05)
    assert detail["status"] == "completed"
    assert all(item["status"] == "completed" for item in detail["targets"])
    assert len(buffers) == 2
    assert all(hashlib.sha256(value).hexdigest() == package["sha256"] for value in buffers.values())
    assert max_chunk <= settings.UPDATE_CHUNK_BYTES
    key_path.unlink(missing_ok=True)


def test_update_package_rejects_unsafe_pip_lock_options(client, tmp_path: Path):
    csrf = _login(client)
    private_key, key_path = _trusted_key()
    package_path = _package(
        tmp_path,
        private_key,
        extra_payload={
            "requirements.lock": (
                b"example==1.0 --hash=sha256:"
                + (b"0" * 64)
                + b" --index-url=https://attacker.invalid/simple\n"
            ),
            "wheelhouse/example-1.0-py3-none-any.whl": b"not-used",
        },
    )
    rejected = client.post(
        "/api/v2/updates/packages",
        headers=csrf,
        files={"file": (package_path.name, package_path.read_bytes(), "application/zip")},
    )
    assert rejected.status_code == 400, rejected.text
    assert "requirements.lock" in rejected.json()["detail"]
    key_path.unlink(missing_ok=True)


def test_update_health_gate_requires_capabilities_and_valid_first_monitor():
    from core.connection_manager import ConnectionManager

    class FakeWebSocket:
        async def close(self, **_kwargs):
            return None

    connection_manager = ConnectionManager()
    websocket = FakeWebSocket()
    asyncio.run(connection_manager.agent_connect(websocket, "health-node"))
    assert not connection_manager.agent_update_ready("health-node", websocket)
    assert connection_manager.mark_agent_capabilities("health-node", websocket)
    assert not connection_manager.agent_update_ready("health-node", websocket)
    assert not connection_manager.mark_agent_monitor(
        "health-node",
        websocket,
        {"cpu_percent": 1, "mem_percent": 2},
    )
    assert connection_manager.mark_agent_monitor(
        "health-node",
        websocket,
        {"cpu_percent": 1, "mem_percent": 2, "disk_percent": 3},
    )
    assert connection_manager.agent_update_ready("health-node", websocket)
    asyncio.run(connection_manager.agent_disconnect(websocket))
    assert not connection_manager.agent_update_ready("health-node", websocket)
