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
    remote_command_allowlist: list[str] = field(default_factory=lambda: [
        value.strip() for value in os.getenv("WCM_REMOTE_COMMAND_ALLOWLIST", "").split(",") if value.strip()
    ])
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("WCM_AGENT_DATA_DIR", "agent_data")))
    transfer_root: Path = field(default_factory=lambda: Path(os.getenv("WCM_TRANSFER_ROOT", "agent_data/transfers")))
    max_transfer_bytes: int = field(
        default_factory=lambda: int(os.getenv("WCM_MAX_TRANSFER_BYTES", str(100 * 1024 * 1024)))
    )
    file_chunk_bytes: int = field(
        default_factory=lambda: int(os.getenv("WCM_FILE_CHUNK_BYTES", str(512 * 1024)))
    )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data.pop("enrollment_token", None)
        data["data_dir"] = str(self.data_dir)
        data["transfer_root"] = str(self.transfer_root)
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
        allowed = {key: value for key, value in data.items() if key in cls.__dataclass_fields__}
        loaded = cls(**allowed)
        loaded.enrollment_token = os.getenv("WCM_ENROLLMENT_TOKEN", "")
        return loaded
