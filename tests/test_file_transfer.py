from __future__ import annotations

import base64
import hashlib
import importlib.util
import os
from pathlib import Path

import pytest


def login(client) -> dict[str, str]:
    response = client.post("/api/v2/auth/login", json={
        "username": "admin", "password": "test-bootstrap-password",
    })
    assert response.status_code == 200
    return {"X-CSRF-Token": client.cookies.get("wcm_csrf")}


def test_chunked_upload_resume_hash_and_limits(client):
    from config import settings

    csrf = login(client)
    data = os.urandom(2 * 1024 * 1024 + 12345)
    file_hash = hashlib.sha256(data).hexdigest()
    created = client.post("/api/v2/files/uploads", headers=csrf, json={
        "filename": "large-test.bin", "size": len(data), "sha256": file_hash,
    })
    assert created.status_code == 201, created.text
    session = created.json()
    upload_id = session["id"]
    chunk_size = session["chunk_size"]

    first = data[:chunk_size]
    first_response = client.put(
        f"/api/v2/files/uploads/{upload_id}/chunks",
        params={"offset": 0, "chunk_sha256": hashlib.sha256(first).hexdigest()},
        headers=csrf,
        files={"chunk": ("chunk.bin", first, "application/octet-stream")},
    )
    assert first_response.status_code == 200, first_response.text

    resumed = client.post("/api/v2/files/uploads", headers=csrf, json={
        "filename": "large-test.bin", "size": len(data), "sha256": file_hash,
    })
    assert resumed.status_code == 201
    assert resumed.json()["id"] == upload_id
    assert resumed.json()["received_bytes"] == len(first)

    bad = data[len(first):len(first) + chunk_size]
    bad_hash = client.put(
        f"/api/v2/files/uploads/{upload_id}/chunks",
        params={"offset": len(first), "chunk_sha256": "0" * 64},
        headers=csrf,
        files={"chunk": ("chunk.bin", bad, "application/octet-stream")},
    )
    assert bad_hash.status_code == 400

    offset = len(first)
    while offset < len(data):
        chunk = data[offset:offset + chunk_size]
        response = client.put(
            f"/api/v2/files/uploads/{upload_id}/chunks",
            params={
                "offset": offset,
                "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            },
            headers=csrf,
            files={"chunk": ("chunk.bin", chunk, "application/octet-stream")},
        )
        assert response.status_code == 200, response.text
        offset = response.json()["next_offset"]

    completed = client.post(
        f"/api/v2/files/uploads/{upload_id}/complete", headers=csrf,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "ready"
    stored = settings.DATA_DIR / "uploads" / f"{upload_id}.bin"
    assert hashlib.sha256(stored.read_bytes()).hexdigest() == file_hash

    traversal = client.post("/api/v2/files/transfers", headers=csrf, json={
        "upload_id": upload_id,
        "target_node_ids": ["missing-node"],
        "dest_path": "../../escape.bin",
        "overwrite": False,
    })
    assert traversal.status_code == 422

    oversized = client.post("/api/v2/files/uploads", headers=csrf, json={
        "filename": "oversized.bin",
        "size": settings.MAX_UPLOAD_BYTES + 1,
        "sha256": "1" * 64,
    })
    assert oversized.status_code == 413


AGENT_MODULE_PATH = Path(__file__).parents[1] / "agent" / "file_transfer.py"
AGENT_SPEC = importlib.util.spec_from_file_location(
    "wcm_agent_file_transfer", AGENT_MODULE_PATH,
)
assert AGENT_SPEC and AGENT_SPEC.loader
AGENT_MODULE = importlib.util.module_from_spec(AGENT_SPEC)
AGENT_SPEC.loader.exec_module(AGENT_MODULE)
FileReceiver = AGENT_MODULE.FileReceiver
validate_relative_path = AGENT_MODULE.validate_relative_path


@pytest.mark.parametrize("path", [
    "../escape.bin",
    "safe/../escape.bin",
    "/etc/passwd",
    r"C:\Windows\system32\evil.bin",
    r"\\server\share\evil.bin",
    "safe/file.txt:stream",
    "CON",
])
def test_agent_rejects_unsafe_destination_paths(path):
    with pytest.raises(ValueError):
        validate_relative_path(path)


def test_agent_resumes_large_file_and_commits_atomically(tmp_path):
    root = tmp_path / "transfer-root"
    receiver = FileReceiver(root, max_bytes=4 * 1024 * 1024, chunk_bytes=512 * 1024)
    data = os.urandom(2 * 1024 * 1024 + 777)
    file_hash = hashlib.sha256(data).hexdigest()
    init_payload = {
        "transfer_id": "transfer-resume-1",
        "dest_path": "nested/final.bin",
        "size": len(data),
        "sha256": file_hash,
        "chunk_size": 512 * 1024,
        "overwrite": False,
    }
    initialized = receiver.init(init_payload)
    assert initialized["success"] and initialized["next_offset"] == 0

    first = data[:512 * 1024]
    first_payload = {
        "transfer_id": "transfer-resume-1",
        "offset": 0,
        "chunk_sha256": hashlib.sha256(first).hexdigest(),
        "content_b64": base64.b64encode(first).decode(),
    }
    written = receiver.chunk(first_payload)
    assert written["success"] and written["next_offset"] == len(first)
    resumed = receiver.init(init_payload)
    assert resumed["success"] and resumed["next_offset"] == len(first)
    replayed = receiver.chunk(first_payload)
    assert replayed["success"] and replayed["next_offset"] == len(first)

    offset = len(first)
    while offset < len(data):
        chunk = data[offset:offset + 512 * 1024]
        response = receiver.chunk({
            "transfer_id": "transfer-resume-1",
            "offset": offset,
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
            "content_b64": base64.b64encode(chunk).decode(),
        })
        assert response["success"], response
        offset = response["next_offset"]

    destination = root / "nested" / "final.bin"
    assert not destination.exists()
    committed = receiver.commit({
        "transfer_id": "transfer-resume-1", "sha256": file_hash,
    })
    assert committed["success"], committed
    assert hashlib.sha256(destination.read_bytes()).hexdigest() == file_hash

    already_complete = receiver.init({
        **init_payload, "transfer_id": "transfer-no-overwrite",
    })
    assert already_complete["success"] and already_complete["completed"]

    different = b"different"
    rejected = receiver.init({
        **init_payload,
        "transfer_id": "transfer-no-overwrite-different",
        "size": len(different),
        "sha256": hashlib.sha256(different).hexdigest(),
    })
    assert not rejected["success"]
    assert "overwrite" in rejected["error"]


def test_agent_rejects_symlink_escape_when_supported(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable for this Windows account")
    receiver = FileReceiver(root)
    result = receiver.init({
        "transfer_id": "symlink-test",
        "dest_path": "link/escape.bin",
        "size": 1,
        "sha256": hashlib.sha256(b"x").hexdigest(),
        "chunk_size": 512 * 1024,
        "overwrite": False,
    })
    assert not result["success"]
    assert "escapes transfer root" in result["error"]
