from __future__ import annotations

import secrets
from pathlib import Path
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Web Cluster Manager"
    VERSION: str = "3.1.0"
    DEBUG: bool = True
    ENVIRONMENT: str = "development"

    # Database (computed property below)

    # JWT
    JWT_SECRET: str = ""
    JWT_SECRET_FILE: Path | None = None
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ACCESS_COOKIE_NAME: str = "wcm_access"
    REFRESH_COOKIE_NAME: str = "wcm_refresh"
    CSRF_COOKIE_NAME: str = "wcm_csrf"
    COOKIE_SECURE: bool = False
    COOKIE_DOMAIN: str | None = None

    # Server
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    TRUSTED_HOSTS: list[str] = ["localhost", "127.0.0.1", "testserver"]

    # Agent WebSocket
    AGENT_HEARTBEAT_INTERVAL: int = 30
    AGENT_HEARTBEAT_TIMEOUT: int = 90
    AGENT_ENROLLMENT_TTL_MINUTES: int = 15
    ENABLE_REMOTE_COMMANDS: bool = False
    REMOTE_COMMAND_ALLOWLIST: list[str] = []

    # CORS
    CORS_ORIGINS: list[str] = ["http://localhost:5173", "http://localhost:8000"]
    LOGIN_RATE_LIMIT_ATTEMPTS: int = 5
    LOGIN_RATE_LIMIT_WINDOW_SECONDS: int = 900

    # File Storage (relative to server directory)
    DATA_DIR: Path = Path(__file__).parent / "data"
    UPDATES_DIR: Path = Path(__file__).parent / "updates"
    LOGS_DIR: Path = Path(__file__).parent / "logs"
    MAX_UPLOAD_BYTES: int = 100 * 1024 * 1024
    FILE_CHUNK_BYTES: int = 512 * 1024
    MAX_TRANSFER_TARGETS: int = 32
    FILE_TRANSFER_CONCURRENCY: int = 4
    FILE_TRANSFER_TIMEOUT_SECONDS: int = 20
    FILE_UPLOAD_PARTIAL_TTL_HOURS: int = 24
    FILE_UPLOAD_RETENTION_HOURS: int = 7 * 24

    # First administrator bootstrap. The password is consumed only when the
    # users table is empty; production deployments should use *_FILE.
    BOOTSTRAP_ADMIN_USERNAME: str = "admin"
    BOOTSTRAP_ADMIN_PASSWORD: str = ""
    BOOTSTRAP_ADMIN_PASSWORD_FILE: Path | None = None

    @property
    def DATABASE_URL(self) -> str:
        return f"sqlite+aiosqlite:///{self.DATA_DIR / 'cluster.db'}"

    model_config = {"env_prefix": "WCM_", "env_file": ".env"}


settings = Settings()


def _read_secret(path: Path | None) -> str:
    if path is None:
        return ""
    return path.read_text(encoding="utf-8").strip()


JWT_SECRET_CONFIGURED = bool(settings.JWT_SECRET or settings.JWT_SECRET_FILE)
if settings.JWT_SECRET_FILE:
    settings.JWT_SECRET = _read_secret(settings.JWT_SECRET_FILE)
if not settings.JWT_SECRET:
    # Development gets an ephemeral key instead of a published default.
    # Production validation in main.py refuses to start without a secret.
    settings.JWT_SECRET = secrets.token_urlsafe(48)

if settings.BOOTSTRAP_ADMIN_PASSWORD_FILE:
    settings.BOOTSTRAP_ADMIN_PASSWORD = _read_secret(settings.BOOTSTRAP_ADMIN_PASSWORD_FILE)
