"""Manual end-to-end acceptance for Server plus two real Agent processes.

This script uses a temporary database and Agent data roots, validates health,
backup, cancellation and selected-node retry, then stops every process and
removes its temporary files. It never starts, stops or targets Docker
containers.
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
from pathlib import Path

import requests


PROJECT_ROOT = Path(__file__).parents[1]
SERVER_ROOT = PROJECT_ROOT / "server"
AGENT_ROOT = PROJECT_ROOT / "agent"
PASSWORD = "e2e-task-center-password"


def _port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _start(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.Popen:
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=flags,
    )


def _stop(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _wait(predicate, message: str, timeout: float = 30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            last = predicate()
            if last:
                return last
        except (requests.RequestException, KeyError, ValueError):
            pass
        time.sleep(0.15)
    raise RuntimeError(f"timed out waiting for {message}; last={last!r}")


def _profiles(data_root: Path, local_root: Path, unique: str) -> None:
    (local_root / "payload").mkdir(parents=True)
    (local_root / "payload" / "identity.txt").write_text(unique, encoding="utf-8")
    (local_root / "logs").mkdir()
    profile = {
        "version": 1,
        "log_profiles": {
            "shared-logs": {
                "root": str(local_root / "logs"),
                "patterns": ["*.log"],
            },
            f"{unique}-logs": {
                "root": str(local_root / "logs"),
                "patterns": ["*.log"],
            },
        },
        "backup_profiles": {
            "shared-backup": {"root": str(local_root)},
            f"{unique}-backup": {"root": str(local_root)},
        },
        "service_profiles": {},
        "command_profiles": {
            "slow-command": {
                "argv": [sys.executable, "-c", "import time; time.sleep(30)"],
                "timeout": 60,
            },
            f"{unique}-identity": {
                "argv": [sys.executable, "--version"],
                "timeout": 10,
            },
        },
    }
    data_root.mkdir(parents=True)
    (data_root / "task_profiles.json").write_text(
        json.dumps(profile, indent=2),
        encoding="utf-8",
    )


def main() -> int:
    runtime = Path(tempfile.mkdtemp(prefix="wcm-task-e2e-"))
    port = _port()
    base = f"http://127.0.0.1:{port}"
    api = f"{base}/api/v2"
    server_process = None
    agents: list[subprocess.Popen | None] = [None, None]
    try:
        server_env = os.environ.copy()
        server_env.update({
            "WCM_DATA_DIR": str(runtime / "server-data"),
            "WCM_UPDATES_DIR": str(runtime / "updates"),
            "WCM_LOGS_DIR": str(runtime / "server-logs"),
            "WCM_BOOTSTRAP_ADMIN_PASSWORD": PASSWORD,
            "WCM_ENABLE_REMOTE_COMMANDS": "true",
            "WCM_DEBUG": "false",
            "WCM_TRUSTED_HOSTS": '["127.0.0.1","localhost"]',
        })
        server_process = _start(
            [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=SERVER_ROOT,
            env=server_env,
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

        def headers() -> dict[str, str]:
            return {"X-CSRF-Token": session.cookies.get("wcm_csrf", "")}

        elevated = session.post(
            f"{api}/auth/step-up",
            headers=headers(),
            json={"password": PASSWORD},
            timeout=5,
        )
        elevated.raise_for_status()

        agent_data = [runtime / "agent-one", runtime / "agent-two"]
        local_roots = [runtime / "root-one", runtime / "root-two"]
        node_ids: list[str] = []
        tokens: list[str] = []
        for index in range(2):
            unique = f"agent-{index + 1}"
            _profiles(agent_data[index], local_roots[index], unique)
            issued = session.post(
                f"{api}/agent-enrollment-tokens",
                headers=headers(),
                json={"label": unique},
                timeout=5,
            )
            issued.raise_for_status()
            tokens.append(issued.json()["token"])

        known_nodes: set[str] = set()
        for index in range(2):
            agent_env = os.environ.copy()
            agent_env.update({
                "WCM_ENROLLMENT_TOKEN": tokens[index],
                "WCM_ENABLE_REMOTE_COMMANDS": "true",
                "WCM_MONITOR_INTERVAL": "1",
            })
            agents[index] = _start(
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
                env=agent_env,
            )

            def new_online_node():
                response = session.get(f"{api}/nodes/", timeout=3)
                response.raise_for_status()
                nodes = [
                    item for item in response.json()
                    if item["status"] == "online" and item["id"] not in known_nodes
                ]
                return nodes[0] if len(nodes) == 1 else None

            node = _wait(new_online_node, f"Agent {index + 1} WebSocket")
            node_ids.append(node["id"])
            known_nodes.add(node["id"])

        targets = {
            "target_node_ids": node_ids,
            "target_group_ids": [],
            "all_online": False,
        }
        resolved = session.post(
            f"{api}/tasks/targets/resolve",
            headers=headers(),
            json=targets,
            timeout=5,
        )
        resolved.raise_for_status()
        common = resolved.json()["common_profiles"]
        assert common["backup_files"] == ["shared-backup"]
        assert common["batch_command"] == ["slow-command"]

        def create_task(task_type: str, params: dict, title: str):
            response = session.post(
                f"{api}/tasks/",
                headers=headers(),
                json={"type": task_type, "title": title, "params": params, **targets},
                timeout=5,
            )
            response.raise_for_status()
            return response.json()["id"]

        def wait_task(task_id: str, statuses: set[str], timeout: float = 30):
            last_detail: dict = {}

            def current():
                nonlocal last_detail
                response = session.get(f"{api}/tasks/{task_id}", timeout=3)
                response.raise_for_status()
                last_detail = response.json()
                return last_detail if last_detail["status"] in statuses else None

            try:
                return _wait(current, f"task {task_id} -> {statuses}", timeout=timeout)
            except RuntimeError as exc:
                raise RuntimeError(f"{exc}; detail={last_detail}") from exc

        health_id = create_task("health_check", {}, "e2e health")
        wait_task(health_id, {"completed"})

        backup_id = create_task(
            "backup_files",
            {"profile": "shared-backup", "source": "payload"},
            "e2e backup",
        )
        backup = wait_task(backup_id, {"completed"}, timeout=60)
        node_to_data = dict(zip(node_ids, agent_data, strict=True))
        for subtask in backup["subtasks"]:
            result = subtask["result"]
            archive = node_to_data[subtask["node_id"]] / result["path"]
            assert archive.is_file()
            assert hashlib.sha256(archive.read_bytes()).hexdigest() == result["sha256"]
            with zipfile.ZipFile(archive) as bundle:
                assert bundle.read("payload/identity.txt").decode() in {"agent-1", "agent-2"}

        command_id = create_task(
            "batch_command",
            {"profile": "slow-command"},
            "e2e cancellation",
        )
        wait_task(command_id, {"running"})
        cancel = session.post(
            f"{api}/tasks/{command_id}/cancel",
            headers=headers(),
            timeout=15,
        )
        cancel.raise_for_status()
        wait_task(command_id, {"cancelled"}, timeout=15)

        _stop(agents[1])
        agents[1] = None

        def second_offline():
            response = session.get(f"{api}/nodes/{node_ids[1]}", timeout=3)
            response.raise_for_status()
            return response.json()["status"] == "offline"

        _wait(second_offline, "second Agent offline")
        partial_id = create_task("health_check", {}, "e2e partial and retry")
        partial = wait_task(partial_id, {"partial"})
        assert {item["status"] for item in partial["subtasks"]} == {"completed", "paused"}

        restart_env = os.environ.copy()
        restart_env["WCM_ENABLE_REMOTE_COMMANDS"] = "true"
        agents[1] = _start(
            [
                sys.executable,
                "main.py",
                "--data-dir",
                str(agent_data[1]),
                "--api",
                api,
                "--server",
                f"ws://127.0.0.1:{port}/ws/agent",
            ],
            cwd=AGENT_ROOT,
            env=restart_env,
        )
        _wait(
            lambda: session.get(f"{api}/nodes/{node_ids[1]}", timeout=3).json()["status"] == "online",
            "second Agent reconnect",
        )
        retry = session.post(
            f"{api}/tasks/{partial_id}/retry",
            headers=headers(),
            json={"target_node_ids": [node_ids[1]]},
            timeout=15,
        )
        retry.raise_for_status()
        wait_task(partial_id, {"completed"})

        print("E2E PASS: profiles, health, backup hash/ZIP, cancellation and selected retry")
        return 0
    finally:
        for process in agents:
            _stop(process)
        _stop(server_process)
        shutil.rmtree(runtime, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
