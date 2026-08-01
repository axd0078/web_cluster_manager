from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent.launcher import resolve_active_release
from agent.main import Agent
from agent.update_helper import UpdateHelper
from agent.update_package import (
    UpdatePackageError,
    canonical_manifest,
    current_runtime_identity,
    verify_update_package,
)
from agent.update_receiver import UpdateReceiver


def _write_public_key(root: Path, private_key: Ed25519PrivateKey) -> Path:
    keys = root / "keys"
    keys.mkdir()
    raw = private_key.public_key().public_bytes(
        serialization.Encoding.Raw,
        serialization.PublicFormat.Raw,
    )
    (keys / "test-key.pub").write_bytes(base64.b64encode(raw))
    return keys


def _write_minimal_wheel(path: Path) -> str:
    files = {
        "wcm_fixture/__init__.py": b"VALUE = 'offline-installed'\n",
        "wcm_fixture-1.0.0.dist-info/METADATA": (
            b"Metadata-Version: 2.1\nName: wcm-fixture\nVersion: 1.0.0\n"
        ),
        "wcm_fixture-1.0.0.dist-info/WHEEL": (
            b"Wheel-Version: 1.0\nGenerator: wcm-tests\n"
            b"Root-Is-Purelib: true\nTag: py3-none-any\n"
        ),
    }
    record_lines = []
    for name, content in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(content).digest()).rstrip(b"=").decode()
        record_lines.append(f"{name},sha256={digest},{len(content)}")
    record_name = "wcm_fixture-1.0.0.dist-info/RECORD"
    files[record_name] = ("\n".join(record_lines) + f"\n{record_name},,\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_package(
    root: Path,
    private_key: Ed25519PrivateKey,
    *,
    release_id: str = "release-4.0.0",
    version: str = "4.0.0",
    files: dict[str, bytes] | None = None,
    extra_entries: dict[str, bytes] | None = None,
    signature: bytes | None = None,
    zip_infos: dict[str, zipfile.ZipInfo] | None = None,
) -> Path:
    runtime = current_runtime_identity()
    payload = files or {
        "main.py": b"print('updated agent')\n",
        "requirements.lock": b"",
    }
    records = [
        {
            "path": path,
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
        for path, content in payload.items()
    ]
    manifest = {
        "schema_version": 1,
        "component": "agent",
        "version": version,
        "release_id": release_id,
        "created_at": "2026-07-31T00:00:00Z",
        "key_id": "test-key",
        "target": {
            "os": runtime.os_family,
            "arch": runtime.arch,
            "python_abi": runtime.python_abi,
        },
        "min_updater_version": "2.0.0",
        "entrypoint": "main.py",
        "files": records,
    }
    encoded_signature = base64.b64encode(
        signature
        if signature is not None
        else private_key.sign(canonical_manifest(manifest))
    )
    package = root / f"{release_id}.wcmupd"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest))
        archive.writestr("manifest.sig", encoded_signature)
        for relative, content in payload.items():
            name = f"payload/{relative}"
            archive.writestr((zip_infos or {}).get(name, name), content)
        for relative, content in (extra_entries or {}).items():
            archive.writestr(relative, content)
    return package


def _receiver(tmp_path: Path, keys: Path) -> UpdateReceiver:
    return UpdateReceiver(
        tmp_path / "spool",
        keys,
        enabled=True,
        active_pointer=tmp_path / "install" / "active.json",
        min_free_bytes=0,
    )


def _transfer_package(
    receiver: UpdateReceiver,
    package: Path,
    *,
    execution_id: str,
    release_id: str,
    version: str,
) -> None:
    raw = package.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    initialized = receiver.init({
        "execution_id": execution_id,
        "package_id": f"package-{execution_id}",
        "release_id": release_id,
        "version": version,
        "size": len(raw),
        "sha256": digest,
        "chunk_size": 512 * 1024,
    })
    assert initialized["success"], initialized
    offset = int(initialized["next_offset"])
    while offset < len(raw):
        chunk = raw[offset:offset + 512 * 1024]
        result = receiver.chunk({
            "execution_id": execution_id,
            "offset": offset,
            "content_b64": base64.b64encode(chunk).decode(),
            "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
        })
        assert result["success"], result
        offset = int(result["next_offset"])
    committed = receiver.commit({
        "execution_id": execution_id,
        "sha256": digest,
    })
    assert committed["success"], committed


def _fake_release_runtime(release: Path, manifest: dict) -> None:
    if os.name == "nt":
        python = release / "venv" / "Scripts" / "python.exe"
    else:
        python = release / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"test python")


def _bootstrap_pointer(install: Path) -> dict:
    release = install / "releases" / "bootstrap-3.1.0"
    (release / "payload").mkdir(parents=True)
    (release / "payload" / "main.py").write_text("print('bootstrap')\n", encoding="utf-8")
    _fake_release_runtime(release, {})
    pointer = {
        "schema_version": 1,
        "release_id": "bootstrap-3.1.0",
        "version": "3.1.0",
        "entrypoint": "main.py",
        "activation_execution_id": "",
        "activation_operation": "",
    }
    install.mkdir(exist_ok=True)
    (install / "active.json").write_text(json.dumps(pointer), encoding="utf-8")
    return pointer


def test_signed_update_package_verification_and_tamper_rejection(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(tmp_path, private_key)

    verified = verify_update_package(package, keys, runtime=current_runtime_identity())
    assert verified.release_id == "release-4.0.0"
    assert verified.version == "4.0.0"
    assert verified.package_sha256 == hashlib.sha256(package.read_bytes()).hexdigest()

    bad_signature = _write_package(
        tmp_path,
        private_key,
        release_id="bad-signature",
        signature=b"\x00" * 64,
    )
    with pytest.raises(UpdatePackageError, match="signature verification"):
        verify_update_package(bad_signature, keys, runtime=current_runtime_identity())

    extra = _write_package(
        tmp_path,
        private_key,
        release_id="extra-file",
        extra_entries={"payload/unlisted.py": b"untrusted"},
    )
    with pytest.raises(UpdatePackageError, match="unlisted files"):
        verify_update_package(extra, keys, runtime=current_runtime_identity())


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "../escape.py",
        "/absolute.py",
        "C:/drive.py",
        "folder\\backslash.py",
        "CON.txt",
        "nested/LPT1.log",
        "trailing./file.py",
    ],
)
def test_update_package_rejects_unsafe_windows_and_traversal_paths(
    tmp_path: Path,
    unsafe_path: str,
):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(
        tmp_path,
        private_key,
        files={
            "main.py": b"print('agent')",
            "requirements.lock": b"",
            unsafe_path: b"unsafe",
        },
    )
    with pytest.raises(UpdatePackageError):
        verify_update_package(package, keys, runtime=current_runtime_identity())


def test_update_package_rejects_symlink_and_excessive_compression_ratio(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    link_info = zipfile.ZipInfo("payload/link.py")
    link_info.create_system = 3
    link_info.external_attr = (stat.S_IFLNK | 0o777) << 16
    symlink = _write_package(
        tmp_path,
        private_key,
        release_id="symlink",
        files={
            "main.py": b"print('agent')",
            "requirements.lock": b"",
            "link.py": b"main.py",
        },
        zip_infos={"payload/link.py": link_info},
    )
    with pytest.raises(UpdatePackageError, match="symlink"):
        verify_update_package(symlink, keys, runtime=current_runtime_identity())

    bomb = _write_package(
        tmp_path,
        private_key,
        release_id="compression-bomb",
        files={
            "main.py": b"print('agent')",
            "requirements.lock": b"",
            "wheelhouse/zeros.whl": b"\x00" * (1024 * 1024),
        },
    )
    with pytest.raises(UpdatePackageError, match="compression ratio"):
        verify_update_package(bomb, keys, runtime=current_runtime_identity())

    nested_wheel = tmp_path / "nested-bomb.whl"
    with zipfile.ZipFile(nested_wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("safe_package/data.bin", b"\x00" * (1024 * 1024))
    nested_bomb = _write_package(
        tmp_path,
        private_key,
        release_id="nested-compression-bomb",
        files={
            "main.py": b"print('agent')",
            "requirements.lock": b"",
            "wheelhouse/nested_bomb-1.0-py3-none-any.whl": nested_wheel.read_bytes(),
        },
    )
    with pytest.raises(UpdatePackageError, match="wheel.*compression ratio"):
        verify_update_package(nested_bomb, keys, runtime=current_runtime_identity())


def test_update_receiver_resumes_chunks_and_commits_only_verified_package(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    wheel = tmp_path / "wcm_fixture-1.0.0-py3-none-any.whl"
    _write_minimal_wheel(wheel)
    package = _write_package(
        tmp_path,
        private_key,
        files={
            "main.py": b"print('agent')",
            "requirements.lock": b"",
            "wheelhouse/wcm_fixture-1.0.0-py3-none-any.whl": wheel.read_bytes(),
            "assets/random.bin": os.urandom(700_000),
        },
    )
    receiver = _receiver(tmp_path, keys)
    raw = package.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    init_payload = {
        "execution_id": "execution-1",
        "package_id": "package-1",
        "release_id": "release-4.0.0",
        "version": "4.0.0",
        "size": len(raw),
        "sha256": digest,
        "chunk_size": 512 * 1024,
    }
    assert receiver.init(init_payload)["next_offset"] == 0
    first = raw[:512 * 1024]
    first_payload = {
        "execution_id": "execution-1",
        "offset": 0,
        "content_b64": base64.b64encode(first).decode(),
        "chunk_sha256": hashlib.sha256(first).hexdigest(),
    }
    first_result = receiver.chunk(first_payload)
    assert first_result["success"]
    assert receiver.init(init_payload)["next_offset"] == len(first)
    assert receiver.chunk(first_payload)["next_offset"] == len(first)

    wrong_offset = receiver.chunk({
        **first_payload,
        "offset": len(first) + 1,
    })
    assert not wrong_offset["success"]
    second = raw[len(first):]
    assert receiver.chunk({
        "execution_id": "execution-1",
        "offset": len(first),
        "content_b64": base64.b64encode(second).decode(),
        "chunk_sha256": hashlib.sha256(second).hexdigest(),
    })["success"]
    committed = receiver.commit({
        "execution_id": "execution-1",
        "sha256": digest,
    })
    assert committed["success"]
    assert committed["release_id"] == "release-4.0.0"
    assert not (tmp_path / "spool" / "incoming" / "execution-1.part").exists()


def test_update_receiver_rebinds_resume_state_to_a_new_attempt(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(
        tmp_path,
        private_key,
        files={
            "main.py": b"print('resume')\n",
            "requirements.lock": b"",
            "payload.bin": os.urandom(700_000),
        },
    )
    receiver = _receiver(tmp_path, keys)
    raw = package.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    stable_transfer_id = "deployment-target-1"
    common = {
        "transfer_id": stable_transfer_id,
        "package_id": "package-resume",
        "release_id": "release-4.0.0",
        "version": "4.0.0",
        "size": len(raw),
        "sha256": digest,
        "chunk_size": 512 * 1024,
    }
    first = receiver.init({**common, "execution_id": "attempt-one"})
    assert first["success"]
    chunk = raw[:200_000]
    written = receiver.chunk({
        "execution_id": "attempt-one",
        "transfer_id": stable_transfer_id,
        "offset": 0,
        "content_b64": base64.b64encode(chunk).decode(),
        "chunk_sha256": hashlib.sha256(chunk).hexdigest(),
    })
    assert written["next_offset"] == len(chunk)

    resumed = receiver.init({**common, "execution_id": "attempt-two"})
    assert resumed["success"]
    assert resumed["next_offset"] == len(chunk)
    offset = len(chunk)
    while offset < len(raw):
        current = raw[offset:offset + 512 * 1024]
        result = receiver.chunk({
            "execution_id": "attempt-two",
            "transfer_id": stable_transfer_id,
            "offset": offset,
            "content_b64": base64.b64encode(current).decode(),
            "chunk_sha256": hashlib.sha256(current).hexdigest(),
        })
        assert result["success"], result
        offset = result["next_offset"]
    committed = receiver.commit({
        "execution_id": "attempt-two",
        "transfer_id": stable_transfer_id,
        "sha256": digest,
    })
    assert committed["success"], committed

    rebound = receiver.init({**common, "execution_id": "attempt-three"})
    assert rebound["success"], rebound
    assert rebound["completed"] is True
    assert rebound["next_offset"] == len(raw)


def test_privileged_helper_switches_only_signed_release_after_health_ack(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(tmp_path, private_key)
    receiver = _receiver(tmp_path, keys)
    _transfer_package(
        receiver,
        package,
        execution_id="execution-activate",
        release_id="release-4.0.0",
        version="4.0.0",
    )
    activated = receiver.activate({
        "execution_id": "execution-activate",
        "deployment_id": "deployment-1",
        "release_id": "release-4.0.0",
        "version": "4.0.0",
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "health_timeout": 120,
    })
    assert activated["success"], activated

    install = tmp_path / "install"
    _bootstrap_pointer(install)
    restarts: list[str] = []
    helper = UpdateHelper(
        install,
        tmp_path / "spool",
        keys,
        restart_agent=lambda: restarts.append("restart"),
    )
    assert helper.process_once()
    active = json.loads((install / "active.json").read_text(encoding="utf-8"))
    assert active["release_id"] == "release-4.0.0"
    assert active["activation_execution_id"] == "execution-activate"
    if os.name == "nt":
        release_python = install / "releases" / "release-4.0.0" / "venv" / "Scripts" / "python.exe"
    else:
        release_python = install / "releases" / "release-4.0.0" / "venv" / "bin" / "python"
    assert release_python.is_file()
    status = receiver.status({"execution_id": "execution-activate"})
    assert status["stage"] == "awaiting_health"
    assert restarts == ["restart"]

    ack = receiver.health_ack({
        "execution_id": "execution-activate",
        "release_id": "release-4.0.0",
        "success": True,
    })
    assert ack["success"]
    assert helper.process_once()
    active = json.loads((install / "active.json").read_text(encoding="utf-8"))
    assert active["activation_execution_id"] == ""
    assert active["activation_operation"] == ""
    assert receiver.status({"execution_id": "execution-activate"})["stage"] == "completed"


def test_privileged_helper_automatically_rolls_back_on_health_timeout(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(tmp_path, private_key)
    receiver = _receiver(tmp_path, keys)
    _transfer_package(
        receiver,
        package,
        execution_id="execution-timeout",
        release_id="release-4.0.0",
        version="4.0.0",
    )
    assert receiver.activate({
        "execution_id": "execution-timeout",
        "deployment_id": "deployment-2",
        "release_id": "release-4.0.0",
        "version": "4.0.0",
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "health_timeout": 120,
    })["success"]

    install = tmp_path / "install"
    bootstrap = _bootstrap_pointer(install)
    clock = [100.0]
    restarts: list[str] = []
    helper = UpdateHelper(
        install,
        tmp_path / "spool",
        keys,
        health_timeout=120,
        now=lambda: clock[0],
        restart_agent=lambda: restarts.append("restart"),
        install_release=_fake_release_runtime,
    )
    assert helper.process_once()
    clock[0] = 221.0
    assert helper.process_once()
    active = json.loads((install / "active.json").read_text(encoding="utf-8"))
    assert active == bootstrap
    status = receiver.status({"execution_id": "execution-timeout"})
    assert status["stage"] == "rolled_back"
    assert "timed out" in status["error"]
    assert restarts == ["restart", "restart"]


def test_rejected_server_health_ack_never_confirms_privileged_update(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    receiver = _receiver(tmp_path, keys)
    rejected = receiver.health_ack({
        "execution_id": "execution-rejected-health",
        "release_id": "release-4.0.0",
        "success": False,
    })
    assert rejected["success"] is False
    assert not (
        tmp_path / "spool" / "health" / "execution-rejected-health.json"
    ).exists()
    accepted = receiver.health_ack({
        "execution_id": "execution-rejected-health",
        "release_id": "release-4.0.0",
        "success": True,
    })
    assert accepted["success"] is True
    assert (
        tmp_path / "spool" / "health" / "execution-rejected-health.json"
    ).is_file()


def test_manual_rollback_requires_exact_verified_history_target(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(tmp_path, private_key)
    receiver = _receiver(tmp_path, keys)
    _transfer_package(
        receiver,
        package,
        execution_id="execution-first",
        release_id="release-4.0.0",
        version="4.0.0",
    )
    assert receiver.activate({
        "execution_id": "execution-first",
        "release_id": "release-4.0.0",
        "version": "4.0.0",
        "package_sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
        "health_timeout": 120,
    })["success"]
    install = tmp_path / "install"
    _bootstrap_pointer(install)
    helper = UpdateHelper(
        install,
        tmp_path / "spool",
        keys,
        restart_agent=lambda: None,
        install_release=_fake_release_runtime,
    )
    helper.process_once()
    receiver.health_ack({
        "execution_id": "execution-first",
        "release_id": "release-4.0.0",
        "success": True,
    })
    helper.process_once()

    queued = receiver.rollback({
        "execution_id": "execution-rollback",
        "deployment_id": "deployment-rollback",
        "target_release_id": "bootstrap-3.1.0",
        "target_version": "3.1.0",
        "health_timeout": 120,
    })
    assert queued["success"], queued
    helper.process_once()
    active = json.loads((install / "active.json").read_text(encoding="utf-8"))
    assert active["release_id"] == "bootstrap-3.1.0"
    assert active["activation_operation"] == "rollback"


def test_launcher_rejects_pointer_entrypoint_escape(tmp_path: Path):
    install = tmp_path / "install"
    pointer = _bootstrap_pointer(install)
    resolved, python, entrypoint = resolve_active_release(install)
    assert resolved == pointer
    assert python.is_file()
    assert entrypoint.name == "main.py"

    pointer["entrypoint"] = "../outside.py"
    (install / "active.json").write_text(json.dumps(pointer), encoding="utf-8")
    with pytest.raises(ValueError, match="entrypoint"):
        resolve_active_release(install)


def test_privileged_helper_never_falls_back_to_network_dependencies(tmp_path: Path):
    install = tmp_path / "install"
    release = tmp_path / "release"
    payload = release / "payload"
    payload.mkdir(parents=True)
    (payload / "requirements.lock").write_text(
        "example==1.0 --hash=sha256:" + ("0" * 64) + "\n",
        encoding="utf-8",
    )
    helper = UpdateHelper(
        install,
        tmp_path / "spool",
        tmp_path / "keys",
        restart_agent=lambda: None,
    )
    with pytest.raises(ValueError, match="offline wheelhouse"):
        helper._install_release_dependencies(release, {})


def test_update_package_rejects_pip_options_in_signed_lock(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    package = _write_package(
        tmp_path,
        private_key,
        files={
            "main.py": b"print('updated agent')\n",
            "requirements.lock": (
                b"example==1.0 --hash=sha256:"
                + (b"0" * 64)
                + b" --extra-index-url=https://attacker.invalid/simple\n"
            ),
            "wheelhouse/example-1.0-py3-none-any.whl": b"unused",
        },
    )
    with pytest.raises(UpdatePackageError, match="requirements.lock"):
        verify_update_package(package, keys)


def test_privileged_dependency_install_is_hash_locked_wheel_only(
    tmp_path: Path,
    monkeypatch,
):
    release = tmp_path / "release"
    payload = release / "payload"
    wheelhouse = payload / "wheelhouse"
    wheelhouse.mkdir(parents=True)
    (payload / "requirements.lock").write_text(
        "example==1.0 --hash=sha256:" + ("0" * 64) + "\n",
        encoding="utf-8",
    )
    (wheelhouse / "example-1.0-py3-none-any.whl").write_bytes(b"placeholder")
    calls: list[list[str]] = []

    class FakeEnvBuilder:
        def __init__(self, **_kwargs):
            pass

        def create(self, target):
            target = Path(target)
            python = (
                target / "Scripts" / "python.exe"
                if os.name == "nt"
                else target / "bin" / "python"
            )
            python.parent.mkdir(parents=True)
            python.write_bytes(b"")

    def fake_run(argv, **_kwargs):
        calls.append([str(value) for value in argv])
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr("agent.update_helper.venv.EnvBuilder", FakeEnvBuilder)
    monkeypatch.setattr("agent.update_helper.subprocess.run", fake_run)
    helper = UpdateHelper(
        tmp_path / "install",
        tmp_path / "spool",
        tmp_path / "keys",
        restart_agent=lambda: None,
    )
    helper._install_release_dependencies(release, {})
    assert calls
    argv = calls[0]
    assert "--no-index" in argv
    assert "--require-hashes" in argv
    assert "--only-binary=:all:" in argv
    assert "--no-deps" in argv


def test_privileged_helper_performs_real_offline_wheel_install(tmp_path: Path):
    release = tmp_path / "release"
    payload = release / "payload"
    wheel = payload / "wheelhouse" / "wcm_fixture-1.0.0-py3-none-any.whl"
    wheel_hash = _write_minimal_wheel(wheel)
    (payload / "requirements.lock").write_text(
        f"wcm-fixture==1.0.0 --hash=sha256:{wheel_hash}\n",
        encoding="utf-8",
    )
    helper = UpdateHelper(
        tmp_path / "install",
        tmp_path / "spool",
        tmp_path / "keys",
        restart_agent=lambda: None,
        min_free_bytes=0,
    )
    helper._install_release_dependencies(release, {})
    python = helper._venv_python(release / "venv")
    result = subprocess.run(
        [str(python), "-I", "-c", "import wcm_fixture; print(wcm_fixture.VALUE)"],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "offline-installed"


def test_agent_and_helper_status_files_are_separated(tmp_path: Path):
    private_key = Ed25519PrivateKey.generate()
    keys = _write_public_key(tmp_path, private_key)
    receiver = _receiver(tmp_path, keys)
    receiver._ensure_layout()
    receiver._write_status("execution-status", {"stage": "activation_queued", "progress": 0})
    helper = UpdateHelper(
        tmp_path / "install",
        tmp_path / "spool",
        keys,
        restart_agent=lambda: None,
    )
    helper._ensure_layout()
    helper._write_status("execution-status", "completed", progress=100)
    assert (tmp_path / "spool" / "agent-status" / "execution-status.json").is_file()
    assert (tmp_path / "spool" / "helper-status" / "execution-status.json").is_file()
    assert receiver.status({"execution_id": "execution-status"})["stage"] == "completed"
    statuses = receiver.changed_statuses(include_all=True)
    assert [item["stage"] for item in statuses] == ["completed"]


def test_agent_update_protocol_keeps_execution_id_separate_from_request_id():
    received: list[dict] = []
    replies: list[tuple[str, str, dict]] = []

    class Receiver:
        def chunk(self, payload: dict) -> dict:
            received.append(payload)
            return {"success": True, "phase": "chunk", "next_offset": 3}

    agent = object.__new__(Agent)
    agent.update_receiver = Receiver()

    async def send_result(message_type: str, request_id: str, payload: dict) -> bool:
        replies.append((message_type, request_id, payload))
        return True

    agent._send_result = send_result
    asyncio.run(agent._handle_message({
        "type": "update_transfer_chunk",
        "request_id": "unique-request-42",
        "payload": {
            "execution_id": "stable-execution",
            "offset": 0,
            "content_b64": "YWJj",
            "chunk_sha256": hashlib.sha256(b"abc").hexdigest(),
        },
    }))
    assert received[0]["execution_id"] == "stable-execution"
    assert received[0]["transfer_id"] == "stable-execution"
    assert replies == [(
        "update_result",
        "unique-request-42",
        {"success": True, "phase": "chunk", "next_offset": 3},
    )]


def test_agent_retries_health_until_server_accepts_current_connection():
    capability_reports: list[bool] = []

    class Receiver:
        def health_ack(self, payload: dict) -> dict:
            return {"success": payload.get("success") is True, "phase": "health_ack"}

    agent = object.__new__(Agent)
    agent.update_receiver = Receiver()
    agent._update_health_pending = True

    async def send_result(*_args, **_kwargs) -> bool:
        return True

    async def send_capabilities() -> None:
        capability_reports.append(True)

    agent._send_result = send_result
    agent._send_capabilities = send_capabilities
    asyncio.run(agent._handle_message({
        "type": "update_health_ack",
        "request_id": "health-rejected",
        "payload": {
            "execution_id": "execution-health",
            "release_id": "release-4.0.0",
            "success": False,
        },
    }))
    assert agent._update_health_pending is True
    assert capability_reports == []

    asyncio.run(agent._handle_message({
        "type": "update_health_ack",
        "request_id": "health-accepted",
        "payload": {
            "execution_id": "execution-health",
            "release_id": "release-4.0.0",
            "success": True,
        },
    }))
    assert agent._update_health_pending is False
    assert capability_reports == [True]
