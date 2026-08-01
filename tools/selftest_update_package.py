#!/usr/bin/env python3
"""Isolated CLI round-trip smoke test for update_package.py."""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


TOOL = Path(__file__).with_name("update_package.py")
REPOSITORY_ROOT = TOOL.parents[1]


def run(arguments: list[str], environment: dict[str, str]) -> dict:
    completed = subprocess.run(
        [sys.executable, str(TOOL), *arguments],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=environment,
    )
    return json.loads(completed.stdout)


def main() -> int:
    environment = os.environ.copy()
    environment["WCM_UPDATE_SIGNING_KEY_PASSWORD"] = (
        "temporary-self-test-password-only"
    )
    with tempfile.TemporaryDirectory(prefix="wcm-update-tool-") as temporary:
        root = Path(temporary)
        source = root / "agent-source"
        source.mkdir()
        (source / "main.py").write_text(
            'print("temporary update package self-test")\n',
            encoding="utf-8",
        )
        (source / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
        wheelhouse = root / "wheelhouse"
        wheelhouse.mkdir()
        wheel = wheelhouse / "demo-1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("demo/__init__.py", "VALUE = 1\n")
        lock = root / "requirements.lock"
        lock.write_text(
            "demo==1.0 --hash=sha256:"
            + hashlib.sha256(wheel.read_bytes()).hexdigest()
            + "\n",
            encoding="utf-8",
        )
        private_key = root / "offline" / "signing.key"
        trusted_keys = root / "trusted-keys"
        package = root / "agent-1.2.3.wcmupd"

        generated = run(
            [
                "keygen",
                "--private-key",
                str(private_key),
                "--public-key-dir",
                str(trusted_keys),
            ],
            environment,
        )
        built = run(
            [
                "build",
                "--source",
                str(source),
                "--output",
                str(package),
                "--private-key",
                str(private_key),
                "--requirements-lock",
                str(lock),
                "--wheelhouse",
                str(wheelhouse),
                "--version",
                "1.2.3",
                "--release-id",
                "self-test-release",
                "--target-os",
                "windows",
                "--target-arch",
                "x86_64",
                "--python-abi",
                "cp311",
            ],
            environment,
        )
        verified = run(
            [
                "verify",
                "--package",
                str(package),
                "--trusted-keys-dir",
                str(trusted_keys),
            ],
            environment,
        )
        if not (
            generated["key_id"] == built["key_id"] == verified["key_id"]
            and verified["valid"] is True
            and verified["file_count"] == 4
        ):
            raise RuntimeError("round-trip validation result is inconsistent")

        sys.path.insert(0, str(REPOSITORY_ROOT))
        from agent.update_package import verify_update_package as agent_verify
        from server.core.update_package import verify_update_package as server_verify
        from tools.update_package import PackageError, normalize_payload_path

        server_result = server_verify(
            package,
            trusted_keys,
            max_package_bytes=100 * 1024 * 1024,
            max_expanded_bytes=500 * 1024 * 1024,
            max_entries=10_000,
        )
        agent_result = agent_verify(package, trusted_keys)
        if (
            server_result.release_id != "self-test-release"
            or agent_result.release_id != "self-test-release"
        ):
            raise RuntimeError("Server or Agent verifier rejected package metadata")
        for unsafe in ("../escape", r"C:\escape", "//server/share", "CON.txt", "name."):
            try:
                normalize_payload_path(unsafe)
            except PackageError:
                continue
            raise RuntimeError(f"unsafe payload path was accepted: {unsafe!r}")

        forbidden_private = REPOSITORY_ROOT / ".forbidden-update-selftest.key"
        refused = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                "keygen",
                "--private-key",
                str(forbidden_private),
                "--public-key-dir",
                str(trusted_keys),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            env=environment,
            check=False,
        )
        if refused.returncode != 2 or forbidden_private.exists():
            raise RuntimeError("repository-local private key was not safely refused")
    print("update package CLI round-trip: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
