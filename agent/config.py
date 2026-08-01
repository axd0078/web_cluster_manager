from __future__ import annotations

import json
import os
import subprocess
import uuid
import getpass
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class AgentConfig:
    server_url: str = "ws://localhost:8000/ws/agent"
    api_url: str = "http://localhost:8000/api/v2"
    agent_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    node_id: str = ""
    agent_token: str = ""
    enrollment_token: str = field(default_factory=lambda: os.getenv("WCM_ENROLLMENT_TOKEN", ""), repr=False)
    client_ip: str = ""
    hostname: str = ""
    os_info: str = ""
    platform: str = "unknown"
    version: str = "3.1.0"
    heartbeat_interval: int = 30
    monitor_interval: int = 5
    docker_interval: int = 30
    ca_cert: str = field(default_factory=lambda: os.getenv("WCM_CA_CERT", ""))
    enable_remote_commands: bool = field(
        default_factory=lambda: os.getenv("WCM_ENABLE_REMOTE_COMMANDS", "false").lower() == "true"
    )
    enable_low_terminal: bool = field(
        default_factory=lambda: os.getenv("WCM_ENABLE_LOW_TERMINAL", "false").lower() == "true"
    )
    terminal_max_sessions: int = field(
        default_factory=lambda: int(os.getenv("WCM_TERMINAL_MAX_SESSIONS", "4"))
    )
    enable_agent_updates: bool = field(
        default_factory=lambda: os.getenv("WCM_ENABLE_AGENT_UPDATES", "false").lower() == "true"
    )
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("WCM_AGENT_DATA_DIR", "agent_data")))
    transfer_root: Path = field(default_factory=lambda: Path(os.getenv("WCM_TRANSFER_ROOT", "agent_data/transfers")))
    update_spool_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("WCM_UPDATE_SPOOL_DIR", "agent_data/update-spool")
        )
    )
    update_trusted_keys_dir: Path = field(
        default_factory=lambda: Path(
            os.getenv("WCM_UPDATE_TRUSTED_KEYS_DIR", "agent_data/trusted_update_keys")
        )
    )
    update_active_pointer: Path = field(
        default_factory=lambda: Path(
            os.getenv("WCM_UPDATE_ACTIVE_POINTER", "agent_data/active.json")
        )
    )
    max_update_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_MAX_UPDATE_BYTES", str(100 * 1024 * 1024))
        )
    )
    max_update_expanded_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_MAX_UPDATE_EXPANDED_BYTES", str(500 * 1024 * 1024))
        )
    )
    max_update_entries: int = field(
        default_factory=lambda: int(os.getenv("WCM_MAX_UPDATE_ENTRIES", "10000"))
    )
    min_update_free_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_MIN_UPDATE_FREE_BYTES", str(256 * 1024 * 1024))
        )
    )
    max_transfer_bytes: int = field(
        default_factory=lambda: int(os.getenv("WCM_MAX_TRANSFER_BYTES", str(100 * 1024 * 1024)))
    )
    max_transfer_total_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_MAX_TRANSFER_TOTAL_BYTES", str(1024 * 1024 * 1024))
        )
    )
    min_transfer_free_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_MIN_TRANSFER_FREE_BYTES", str(256 * 1024 * 1024))
        )
    )
    transfer_partial_ttl_seconds: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_TRANSFER_PARTIAL_TTL_SECONDS", str(24 * 60 * 60))
        )
    )
    file_chunk_bytes: int = field(
        default_factory=lambda: int(os.getenv("WCM_FILE_CHUNK_BYTES", str(512 * 1024)))
    )
    task_concurrency: int = field(
        default_factory=lambda: int(os.getenv("WCM_TASK_CONCURRENCY", "2"))
    )
    task_backup_retention_days: int = field(
        default_factory=lambda: int(os.getenv("WCM_TASK_BACKUP_RETENTION_DAYS", "90"))
    )
    task_backup_total_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_TASK_BACKUP_TOTAL_BYTES", str(2 * 1024 * 1024 * 1024))
        )
    )
    task_backup_min_free_bytes: int = field(
        default_factory=lambda: int(
            os.getenv("WCM_TASK_BACKUP_MIN_FREE_BYTES", str(256 * 1024 * 1024))
        )
    )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("enrollment_token", None)
        for key in (
            "data_dir",
            "transfer_root",
            "update_spool_dir",
            "update_trusted_keys_dir",
            "update_active_pointer",
        ):
            data[key] = str(data[key])
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        try:
            path.chmod(0o600)
        except OSError:
            pass
        if os.name == "nt":
            # ProgramData/Program Files may inherit read access for local users;
            # explicitly restrict the credential file to its owner and SYSTEM.
            try:
                subprocess.run(
                    [
                        "icacls", str(path), "/inheritance:r", "/grant:r",
                        f"{getpass.getuser()}:(F)", "*S-1-5-18:(F)",
                    ],
                    check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            except (OSError, subprocess.CalledProcessError):
                pass

    @classmethod
    def load(cls, path: Path) -> AgentConfig:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return cls()
        data["data_dir"] = Path(data.get("data_dir", "agent_data"))
        data["transfer_root"] = Path(data.get("transfer_root", "agent_data/transfers"))
        data["update_spool_dir"] = Path(
            data.get("update_spool_dir", "agent_data/update-spool")
        )
        data["update_trusted_keys_dir"] = Path(
            data.get("update_trusted_keys_dir", "agent_data/trusted_update_keys")
        )
        data["update_active_pointer"] = Path(
            data.get("update_active_pointer", "agent_data/active.json")
        )
        allowed = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        loaded = cls(**allowed)
        loaded.enrollment_token = os.getenv("WCM_ENROLLMENT_TOKEN", "")
        return loaded
