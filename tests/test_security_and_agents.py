from __future__ import annotations

import time

from starlette.websockets import WebSocketDisconnect


def login(client):
    response = client.post("/api/v2/auth/login", json={
        "username": "admin", "password": "test-bootstrap-password",
    })
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("wcm_csrf")}


def test_public_registration_removed_and_csrf_enforced(client):
    assert client.post("/api/v2/auth/register", json={
        "username": "attacker", "password": "attacker-password", "role": "admin",
    }).status_code in {404, 405}
    login(client)
    assert client.post("/api/v2/agent-enrollment-tokens", json={}).status_code == 403


def test_unknown_api_does_not_fall_back_to_spa(client):
    response = client.get("/api/v2/does-not-exist")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/json")


def test_logout_revokes_existing_session_tokens(client):
    csrf = login(client)
    old_access = client.cookies.get("wcm_access")
    assert client.post("/api/v2/auth/logout", headers=csrf).status_code == 204
    client.cookies.set("wcm_access", old_access)
    assert client.get("/api/v2/auth/me").status_code == 401


def test_single_use_enrollment_and_live_metrics(client):
    csrf = login(client)
    created = client.post("/api/v2/agent-enrollment-tokens", headers=csrf, json={"label": "pytest"})
    assert created.status_code == 201
    raw = created.json()["token"]
    body = {
        "agent_id": "b" * 32, "ip": "10.20.30.40", "hostname": "docker-test",
        "os": "Linux", "platform": "docker-host", "version": "3.1.0",
        "capabilities": {"docker": True},
    }
    enrolled = client.post("/api/v2/agents/enroll", headers={"Authorization": f"Bearer {raw}"}, json=body)
    assert enrolled.status_code == 201
    assert client.post("/api/v2/agents/enroll", headers={"Authorization": f"Bearer {raw}"}, json=body).status_code == 401

    token = enrolled.json()["agent_token"]
    with client.websocket_connect("/ws/agent", headers={"Authorization": f"Bearer {token}"}) as ws:
        ws.send_json({"type": "monitor_data", "payload": {"cpu_percent": 7.0, "mem_percent": 8.0, "disk_percent": 9.0}})
        ws.send_json({"type": "container_inventory", "payload": {"containers": [{
            "runtime_id": "c" * 64, "name": "nginx", "image": "nginx:alpine",
            "state": "running", "status": "Up", "cpu_percent": 1.0, "mem_percent": 2.0,
        }]}})
        time.sleep(0.2)
        metrics = client.get("/api/v2/monitor/current").json()
        nodes = client.get("/api/v2/nodes/").json()
        assert metrics[0]["cpu_percent"] == 7.0
        assert nodes[0]["containers"][0]["name"] == "nginx"


def test_unauthenticated_websockets_are_rejected(client):
    try:
        with client.websocket_connect("/ws/agent"):
            raise AssertionError("unauthenticated Agent websocket was accepted")
    except WebSocketDisconnect as exc:
        assert exc.code == 4001
    try:
        with client.websocket_connect("/ws/frontend"):
            raise AssertionError("unauthenticated frontend websocket was accepted")
    except WebSocketDisconnect as exc:
        assert exc.code == 4001


def test_update_filename_traversal_is_rejected(client):
    csrf = login(client)
    response = client.post(
        "/api/v2/updates/packages", params={"version": "bad"}, headers=csrf,
        files={"file": ("../../escape.zip", b"not-a-zip", "application/zip")},
    )
    assert response.status_code == 400
