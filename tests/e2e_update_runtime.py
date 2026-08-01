"""Manual end-to-end acceptance for trusted Agent updates.

The script starts one isolated Server and two real Agent processes, builds and
uploads a signed package larger than 2 MiB, lets the privileged filesystem-only
UpdateHelper activate each release, and verifies the manual canary gate before
the second node is updated. All state, credentials, ports and signing material
are temporary. Docker containers are never used as update targets or changed.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable

import requests


PROJECT_ROOT = Path(__file__).parents[1]
SERVER_ROOT = PROJECT_ROOT / "server"
AGENT_ROOT = PROJECT_ROOT / "agent"
PASSWORD = "e2e-update-center-password"

sys.path.insert(0, str(PROJECT_ROOT))

from agent.update_helper import UpdateHelper  # noqa: E402
from agent.update_package import current_runtime_identity  # noqa: E402
from tools.update_package import build_package, generate_keypair  # noqa: E402


@dataclass
class ManagedProcess:
    process: subprocess.Popen
    output: BinaryIO
    log_path: Path


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    log_path: Path,
) -> ManagedProcess:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    output = log_path.open("ab", buffering=0)
    try:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=output,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except Exception:
        output.close()
        raise
    return ManagedProcess(process=process, output=output, log_path=log_path)


def _stop(managed: ManagedProcess | None) -> None:
    if managed is None:
        return
    process = managed.process
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=8)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    managed.output.close()


def _log_tail(managed: ManagedProcess | None, limit: int = 8_000) -> str:
    if managed is None:
        return "<not started>"
    try:
        managed.output.flush()
        content = managed.log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<log unavailable>"
    return content[-limit:]


def _wait(predicate: Callable[[], object], message: str, timeout: float = 45):
    deadline = time.monotonic() + timeout
    last: object = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (requests.RequestException, KeyError, ValueError, OSError):
            pass
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {message}; last={last!r}")


def _csrf(session: requests.Session) -> dict[str, str]:
    return {"X-CSRF-Token": session.cookies.get("wcm_csrf", "")}


def _fake_release_runtime(release: Path, _manifest: dict) -> None:
    if os.name == "nt":
        python = release / "venv" / "Scripts" / "python.exe"
    else:
        python = release / "venv" / "bin" / "python"
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_bytes(b"e2e placeholder for the already tested offline venv installer")


def _bootstrap_install(install_root: Path) -> None:
    release = install_root / "releases" / "bootstrap-3.1.0"
    (release / "payload").mkdir(parents=True)
    (release / "payload" / "main.py").write_text(
        "print('bootstrap Agent')\n",
        encoding="utf-8",
    )
    _fake_release_runtime(release, {})
    install_root.mkdir(parents=True, exist_ok=True)
    (install_root / "active.json").write_text(
        json.dumps({
            "schema_version": 1,
            "release_id": "bootstrap-3.1.0",
            "version": "3.1.0",
            "entrypoint": "main.py",
            "activation_execution_id": "",
            "activation_operation": "",
        }),
        encoding="utf-8",
    )


def _build_signed_package(runtime: Path, trusted_keys: Path) -> tuple[Path, Path, str]:
    key_password_name = "WCM_E2E_SIGNING_KEY_PASSWORD"
    private_key = runtime / "offline-signing-key.pem"
    os.environ[key_password_name] = "ephemeral-e2e-signing-password"
    try:
        _, public_key = generate_keypair(private_key, trusted_keys, key_password_name)

        source = runtime / "package-source"
        source.mkdir()
        (source / "main.py").write_text(
            "print('trusted updated Agent')\n",
            encoding="utf-8",
        )
        payload = source / "assets" / "random.bin"
        payload.parent.mkdir()
        payload.write_bytes(os.urandom(2_600_000))

        wheelhouse = runtime / "wheelhouse"
        wheelhouse.mkdir()
        wheel = wheelhouse / "e2e_fixture-1.0.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("e2e_fixture/__init__.py", "VALUE = 1\n")
        requirements = runtime / "requirements.lock"
        requirements.write_text(
            "e2e-fixture==1.0.0 --hash=sha256:"
            f"{hashlib.sha256(wheel.read_bytes()).hexdigest()}\n",
            encoding="utf-8",
        )

        identity = current_runtime_identity()
        package = runtime / "agent-4.1.0.wcmupd"
        built = build_package(
            source_root=source,
            output=package,
            private_key_path=private_key,
            password_env=key_password_name,
            requirements_lock=requirements,
            wheelhouse=wheelhouse,
            version="4.1.0",
            release_id="e2e-release-4.1.0",
            target_os=identity.os_family,
            target_arch=identity.arch,
            python_abi=identity.python_abi,
            min_updater_version="2.0.0",
            entrypoint="main.py",
        )
        assert package.stat().st_size > 2 * 1024 * 1024
        assert built["package_sha256"] == hashlib.sha256(package.read_bytes()).hexdigest()
        return package, public_key, hashlib.sha256(payload.read_bytes()).hexdigest()
    finally:
        os.environ.pop(key_password_name, None)


def _port_is_released(port: int) -> bool:
    with socket.socket() as sock:
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def main() -> int:
    runtime = Path(tempfile.mkdtemp(prefix="wcm-update-e2e-"))
    port = _port()
    base = f"http://127.0.0.1:{port}"
    api = f"{base}/api/v2"
    server: ManagedProcess | None = None
    agents: list[ManagedProcess | None] = [None, None]
    agent_envs: list[dict[str, str]] = []
    helpers: list[UpdateHelper] = []
    deployment_detail: dict = {}

    try:
        server_keys = runtime / "server-trusted-keys"
        server_keys.mkdir()
        package, public_key, source_payload_sha256 = _build_signed_package(
            runtime, server_keys,
        )
        package_sha256 = hashlib.sha256(package.read_bytes()).hexdigest()

        server_env = os.environ.copy()
        server_env.update({
            "WCM_DATA_DIR": str(runtime / "server-data"),
            "WCM_UPDATES_DIR": str(runtime / "server-updates"),
            "WCM_LOGS_DIR": str(runtime / "server-logs"),
            "WCM_UPDATE_TRUSTED_KEYS_DIR": str(server_keys),
            "WCM_BOOTSTRAP_ADMIN_PASSWORD": PASSWORD,
            "WCM_JWT_SECRET": "isolated-e2e-jwt-secret-with-sufficient-entropy-123456",
            "WCM_DEBUG": "false",
            "WCM_COOKIE_SECURE": "false",
            "WCM_TRUSTED_HOSTS": '["127.0.0.1","localhost"]',
        })
        server = _start(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
            ],
            cwd=SERVER_ROOT,
            env=server_env,
            log_path=runtime / "server.log",
        )
        _wait(
            lambda: requests.get(f"{api}/health", timeout=1).status_code == 200,
            "temporary Server startup",
        )

        session = requests.Session()
        login = session.post(
            f"{api}/auth/login",
            json={"username": "admin", "password": PASSWORD},
            timeout=5,
        )
        login.raise_for_status()
        elevated = session.post(
            f"{api}/auth/step-up",
            headers=_csrf(session),
            json={"password": PASSWORD},
            timeout=5,
        )
        elevated.raise_for_status()

        agent_data = [runtime / "agent-one", runtime / "agent-two"]
        install_roots = [runtime / "install-one", runtime / "install-two"]
        spool_roots = [root / "update-spool" for root in agent_data]
        agent_keys = [root / "trusted-update-keys" for root in agent_data]
        node_ids: list[str] = []
        tokens: list[str] = []

        for index in range(2):
            agent_data[index].mkdir()
            agent_keys[index].mkdir()
            shutil.copy2(public_key, agent_keys[index] / public_key.name)
            _bootstrap_install(install_roots[index])
            issued = session.post(
                f"{api}/agent-enrollment-tokens",
                headers=_csrf(session),
                json={"label": f"update-e2e-agent-{index + 1}"},
                timeout=5,
            )
            issued.raise_for_status()
            tokens.append(issued.json()["token"])

            environment = os.environ.copy()
            environment.update({
                "WCM_ENROLLMENT_TOKEN": tokens[index],
                "WCM_ENABLE_AGENT_UPDATES": "true",
                "WCM_UPDATE_SPOOL_DIR": str(spool_roots[index]),
                "WCM_UPDATE_TRUSTED_KEYS_DIR": str(agent_keys[index]),
                "WCM_UPDATE_ACTIVE_POINTER": str(install_roots[index] / "active.json"),
                "WCM_MIN_UPDATE_FREE_BYTES": "0",
            })
            agent_envs.append(environment)

        def launch_agent(index: int) -> ManagedProcess:
            return _start(
                [
                    sys.executable,
                    "main.py",
                    "--data-dir",
                    str(agent_data[index]),
                    "--api",
                    api,
                    "--server",
                    f"ws://127.0.0.1:{port}/ws/agent",
                ],
                cwd=AGENT_ROOT,
                env=agent_envs[index],
                log_path=runtime / f"agent-{index + 1}.log",
            )

        known_nodes: set[str] = set()
        for index in range(2):
            agents[index] = launch_agent(index)

            def new_online_node():
                response = session.get(f"{api}/nodes/", timeout=3)
                response.raise_for_status()
                nodes = [
                    item for item in response.json()
                    if item["status"] == "online" and item["id"] not in known_nodes
                ]
                return nodes[0] if len(nodes) == 1 else None

            node = _wait(new_online_node, f"Agent {index + 1} WebSocket enrollment")
            node_ids.append(node["id"])
            known_nodes.add(node["id"])

        def restart_agent(index: int) -> None:
            _stop(agents[index])
            agents[index] = launch_agent(index)

        for index in range(2):
            helpers.append(UpdateHelper(
                install_roots[index],
                spool_roots[index],
                agent_keys[index],
                restart_agent=lambda index=index: restart_agent(index),
                install_release=_fake_release_runtime,
            ))

        with package.open("rb") as package_file:
            uploaded = session.post(
                f"{api}/updates/packages",
                headers=_csrf(session),
                files={
                    "file": (
                        package.name,
                        package_file,
                        "application/octet-stream",
                    ),
                },
                data={"description": "isolated two-Agent E2E package"},
                timeout=30,
            )
        uploaded.raise_for_status()
        package_record = uploaded.json()
        assert package_record["sha256"] == package_sha256
        assert package_record["size"] == package.stat().st_size

        targets = {
            "package_id": package_record["id"],
            "target_node_ids": node_ids,
            "target_group_ids": [],
            "all_online": False,
        }

        def compatible_targets():
            response = session.post(
                f"{api}/updates/targets/resolve",
                headers=_csrf(session),
                json=targets,
                timeout=5,
            )
            if response.status_code != 200:
                return None
            values = response.json()["nodes"]
            return values if len(values) == 2 and all(item["compatible"] for item in values) else None

        resolved = _wait(compatible_targets, "both Agents to report update v2 capabilities")
        assert {item["id"] for item in resolved} == set(node_ids)

        created = session.post(
            f"{api}/updates/deployments",
            headers=_csrf(session),
            json={**targets, "canary_node_ids": [node_ids[0]]},
            timeout=10,
        )
        created.raise_for_status()
        deployment_id = created.json()["id"]

        def get_deployment() -> dict:
            response = session.get(
                f"{api}/updates/deployments/{deployment_id}",
                timeout=5,
            )
            response.raise_for_status()
            return response.json()

        def activate_target(index: int) -> dict:
            node_id = node_ids[index]

            def ready_for_helper():
                nonlocal deployment_detail
                deployment_detail = get_deployment()
                target = next(
                    item for item in deployment_detail["targets"]
                    if item["node_id"] == node_id
                )
                execution_id = str(target.get("execution_id") or "")
                request = spool_roots[index] / "requests" / f"{execution_id}.json"
                return target if target["phase"] == "health_check" and request.is_file() else None

            target = _wait(
                ready_for_helper,
                f"Agent {index + 1} chunk transfer, signature verification and activation request",
                timeout=60,
            )
            execution_id = target["execution_id"]
            assert target["bytes_sent"] == package_record["size"]
            assert helpers[index].process_once()

            active = json.loads((install_roots[index] / "active.json").read_text(encoding="utf-8"))
            assert active["release_id"] == package_record["release_id"]
            assert active["activation_execution_id"] == execution_id

            health_path = spool_roots[index] / "health" / f"{execution_id}.json"
            _wait(
                lambda: health_path.is_file(),
                f"Agent {index + 1} healthy reconnect acknowledgement",
                timeout=45,
            )
            _wait(
                lambda: helpers[index].process_once()
                and not helpers[index].pending_path.exists(),
                f"UpdateHelper {index + 1} health finalization",
                timeout=15,
            )

            status = json.loads(
                (spool_roots[index] / "helper-status" / f"{execution_id}.json").read_text(
                    encoding="utf-8"
                )
            )
            assert status["stage"] == "completed"
            active = json.loads((install_roots[index] / "active.json").read_text(encoding="utf-8"))
            assert active["activation_execution_id"] == ""
            extracted = (
                install_roots[index]
                / "releases"
                / package_record["release_id"]
                / "payload"
                / "assets"
                / "random.bin"
            )
            assert hashlib.sha256(extracted.read_bytes()).hexdigest() == source_payload_sha256
            return status

        activate_target(0)

        def awaiting_approval():
            nonlocal deployment_detail
            deployment_detail = get_deployment()
            if deployment_detail["status"] != "awaiting_approval":
                return None
            first = next(
                item for item in deployment_detail["targets"]
                if item["node_id"] == node_ids[0]
            )
            second = next(
                item for item in deployment_detail["targets"]
                if item["node_id"] == node_ids[1]
            )
            second_active = json.loads(
                (install_roots[1] / "active.json").read_text(encoding="utf-8")
            )
            return (
                deployment_detail
                if first["status"] == "completed"
                and second["status"] == "queued"
                and second_active["release_id"] == "bootstrap-3.1.0"
                else None
            )

        _wait(awaiting_approval, "manual canary approval gate", timeout=30)
        approved = session.post(
            f"{api}/updates/deployments/{deployment_id}/approve",
            headers=_csrf(session),
            timeout=10,
        )
        approved.raise_for_status()
        activate_target(1)

        def completed():
            nonlocal deployment_detail
            deployment_detail = get_deployment()
            return deployment_detail if deployment_detail["status"] == "completed" else None

        final = _wait(completed, "two-Agent update deployment completion", timeout=30)
        assert all(target["status"] == "completed" for target in final["targets"])
        assert all(target["attempts"] == 1 for target in final["targets"])
        assert all(target["bytes_sent"] == package_record["size"] for target in final["targets"])
        for node_id in node_ids:
            version = session.get(f"{api}/updates/nodes/{node_id}/version", timeout=5)
            version.raise_for_status()
            assert version.json()["version"] == "4.1.0"
            assert version.json()["active_release_id"] == "e2e-release-4.1.0"

        print(
            "PASS: signed package",
            package_record["size"],
            "bytes; 512 KiB chunked transfer; canary gate; two real Agent restarts;",
            "UpdateHelper health finalization; deployment completed",
        )
        return 0
    except Exception as exc:
        details = [f"E2E failure: {exc}"]
        if deployment_detail:
            details.append("last deployment: " + json.dumps(deployment_detail, ensure_ascii=False))
        details.append("server log:\n" + _log_tail(server))
        for index, agent in enumerate(agents):
            details.append(f"Agent {index + 1} log:\n" + _log_tail(agent))
        raise RuntimeError("\n\n".join(details)) from exc
    finally:
        for agent in agents:
            _stop(agent)
        _stop(server)
        _wait(lambda: _port_is_released(port), "temporary Server port release", timeout=10)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
