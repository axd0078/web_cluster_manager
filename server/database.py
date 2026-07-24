from __future__ import annotations

import shutil
import sqlite3
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

CURRENT_SCHEMA_VERSION = 4

engine = create_async_engine(settings.DATABASE_URL, echo=settings.DEBUG)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _configure_sqlite(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=5000")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db():
    from models import node, user  # noqa: F401 — ensure models are loaded
    _backup_before_migration()
    async with engine.begin() as conn:
        # Fresh databases are created with the current schema; existing tables
        # are left untouched and then upgraded by the idempotent statements.
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at DATETIME NOT NULL)"
        ))
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 1"))
        if result.scalar_one_or_none() is None:
            columns = await conn.execute(text("PRAGMA table_info(nodes)"))
            existing = {row[1] for row in columns.fetchall()}
            additions = {
                "agent_id": "VARCHAR(36)",
                "platform": "VARCHAR(30) NOT NULL DEFAULT 'unknown'",
                "capabilities": "TEXT NOT NULL DEFAULT '{}'",
                "credential_state": "VARCHAR(30) NOT NULL DEFAULT 'reenrollment_required'",
            }
            for name, definition in additions.items():
                if name not in existing:
                    await conn.execute(text(f"ALTER TABLE nodes ADD COLUMN {name} {definition}"))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_nodes_agent_id ON nodes(agent_id)"
            ))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (1, :now)"),
                {"now": datetime.now(timezone.utc)},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 2"))
        if result.scalar_one_or_none() is None:
            columns = await conn.execute(text("PRAGMA table_info(update_packages)"))
            existing = {row[1] for row in columns.fetchall()}
            if "sha256" not in existing:
                await conn.execute(text("ALTER TABLE update_packages ADD COLUMN sha256 VARCHAR(64)"))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (2, :now)"),
                {"now": datetime.now(timezone.utc)},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 3"))
        if result.scalar_one_or_none() is None:
            columns = await conn.execute(text("PRAGMA table_info(users)"))
            existing = {row[1] for row in columns.fetchall()}
            if "token_version" not in existing:
                await conn.execute(text("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0"))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (3, :now)"),
                {"now": datetime.now(timezone.utc)},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 4"))
        if result.scalar_one_or_none() is None:
            columns = await conn.execute(text("PRAGMA table_info(file_transfers)"))
            existing = {row[1] for row in columns.fetchall()}
            additions = {
                "sha256": "VARCHAR(64)",
                "dest_path": "VARCHAR(500)",
                "overwrite": "BOOLEAN NOT NULL DEFAULT 0",
                "created_by": "VARCHAR(36)",
                "started": "DATETIME",
                "finished": "DATETIME",
            }
            for name, definition in additions.items():
                if name not in existing:
                    await conn.execute(text(
                        f"ALTER TABLE file_transfers ADD COLUMN {name} {definition}"
                    ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_file_transfers_created_by "
                "ON file_transfers(created_by)"
            ))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (4, :now)"),
                {"now": datetime.now(timezone.utc)},
            )


def _backup_before_migration() -> None:
    """Create one timestamped backup before applying the next schema migration."""
    db_path = Path(settings.DATA_DIR) / "cluster.db"
    if not db_path.exists() or db_path.stat().st_size == 0:
        return
    try:
        with sqlite3.connect(db_path) as connection:
            has_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
            ).fetchone()
            if has_table:
                migrated = connection.execute(
                    "SELECT 1 FROM schema_migrations WHERE version=?",
                    (CURRENT_SCHEMA_VERSION,),
                ).fetchone()
                if migrated:
                    return
    except sqlite3.DatabaseError:
        # Let the async initialization surface the database error normally.
        return

    backup_dir = Path(settings.DATA_DIR) / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    shutil.copy2(
        db_path,
        backup_dir / f"cluster_pre_v{CURRENT_SCHEMA_VERSION}_{stamp}.db",
    )
