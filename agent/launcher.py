from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path, PurePosixPath, PureWindowsPath


RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
VERSION_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"
)
EXECUTION_ID_RE = re.compile(r"^[A-Za-z0-9_-]{0,80}$")


def _regular_file(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return stat.S_ISREG(info.st_mode) and not path.is_symlink()


def _relative_entrypoint(value: object) -> PurePosixPath:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError("active Agent entrypoint is invalid")
    if len(value) > 500 or "\x00" in value or "\\" in value:
        raise ValueError("active Agent entrypoint is invalid")
    windows = PureWindowsPath(value)
    posix = PurePosixPath(value)
    if windows.is_absolute() or windows.drive or posix.is_absolute():
        raise ValueError("active Agent entrypoint must be relative")
    if any(part in {"", ".", ".."} or ":" in part for part in posix.parts):
        raise ValueError("active Agent entrypoint contains an unsafe component")
    if not value.endswith(".py"):
        raise ValueError("active Agent entrypoint is not Python source")
    return posix


def resolve_active_release(install_root: Path) -> tuple[dict, Path, Path]:
    install_root = install_root.resolve(strict=True)
    pointer_path = install_root / "active.json"
    if not _regular_file(pointer_path) or pointer_path.stat().st_size > 64 * 1024:
        raise ValueError("active Agent release pointer is unavailable")
    try:
        pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("active Agent release pointer is invalid") from exc
    required = {
        "schema_version",
        "release_id",
        "version",
        "entrypoint",
        "activation_execution_id",
        "activation_operation",
    }
    if not isinstance(pointer, dict) or set(pointer) != required:
        raise ValueError("active Agent release pointer schema is invalid")
    release_id = pointer.get("release_id")
    version = pointer.get("version")
    execution_id = pointer.get("activation_execution_id")
    operation = pointer.get("activation_operation")
    if (
        pointer.get("schema_version") != 1
        or not isinstance(release_id, str)
        or not RELEASE_ID_RE.fullmatch(release_id)
        or not isinstance(version, str)
        or not VERSION_RE.fullmatch(version)
        or not isinstance(execution_id, str)
        or not EXECUTION_ID_RE.fullmatch(execution_id)
        or operation not in {"", "activate", "rollback"}
    ):
        raise ValueError("active Agent release pointer fields are invalid")
    relative_entrypoint = _relative_entrypoint(pointer.get("entrypoint"))

    releases_root = (install_root / "releases").resolve(strict=True)
    release_root = (releases_root / release_id).resolve(strict=True)
    if releases_root not in release_root.parents or release_root.is_symlink():
        raise ValueError("active Agent release escapes the release root")
    payload_root = (release_root / "payload").resolve(strict=True)
    entrypoint = (
        payload_root / Path(*relative_entrypoint.parts)
    ).resolve(strict=True)
    if payload_root not in entrypoint.parents or not _regular_file(entrypoint):
        raise ValueError("active Agent entrypoint escapes its signed payload")
    if os.name == "nt":
        python = release_root / "venv" / "Scripts" / "python.exe"
    else:
        python = release_root / "venv" / "bin" / "python"
    if not _regular_file(python):
        raise ValueError("active Agent virtual environment is incomplete")
    return pointer, python, entrypoint


def main() -> int:
    if os.name == "nt":
        default_root = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "WebClusterAgent"
    else:
        default_root = Path("/opt/web-cluster-agent")
    parser = argparse.ArgumentParser(description="Stable Web Cluster Agent launcher")
    parser.add_argument("--install-root", default=str(default_root))
    parser.add_argument("--agent-data-dir", default=None)
    args, agent_args = parser.parse_known_args()
    try:
        pointer, python, entrypoint = resolve_active_release(Path(args.install_root))
        environment = dict(os.environ)
        environment.pop("PYTHONHOME", None)
        environment.pop("PYTHONPATH", None)
        environment["PYTHONNOUSERSITE"] = "1"
        environment["WCM_ACTIVE_RELEASE_ID"] = pointer["release_id"]
        environment["WCM_ACTIVE_VERSION"] = pointer["version"]
        environment["WCM_UPDATE_EXECUTION_ID"] = pointer["activation_execution_id"]
        environment["WCM_UPDATE_ACTIVE_POINTER"] = str(
            Path(args.install_root).resolve() / "active.json"
        )
        environment["WCM_UPDATE_TRUSTED_KEYS_DIR"] = str(
            Path(args.install_root).resolve() / "trusted_update_keys"
        )
        if args.agent_data_dir:
            data_dir = Path(args.agent_data_dir).resolve()
        elif os.name == "nt":
            data_dir = (
                Path(os.getenv("ProgramData", r"C:\ProgramData"))
                / "WebClusterAgent"
            )
        else:
            data_dir = Path("/var/lib/web-cluster-agent")
        environment["WCM_AGENT_DATA_DIR"] = str(data_dir)
        environment["WCM_UPDATE_SPOOL_DIR"] = str(data_dir / "update-spool")
        environment["WCM_ENABLE_AGENT_UPDATES"] = "true"
        if "--data-dir" not in agent_args:
            agent_args.extend(["--data-dir", str(data_dir)])
        os.chdir(entrypoint.parent)
        os.execve(
            str(python),
            [str(python), "-I", str(entrypoint), *agent_args],
            environment,
        )
    except Exception:
        # Keep request-controlled data and local installation paths out of the
        # service log. The privileged helper performs detailed local status.
        print("stable Agent launcher could not start the active release", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
