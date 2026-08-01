from __future__ import annotations

import sqlite3
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from config import settings

CURRENT_SCHEMA_VERSION = 8

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
    from models import node, terminal, user  # noqa: F401 — ensure models are loaded
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
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 5"))
        if result.scalar_one_or_none() is None:
            # Task ownership used to store the mutable username. Convert every
            # resolvable owner to the stable user id before enforcing object
            # authorization in the API.
            await conn.execute(text(
                "UPDATE tasks SET created_by = ("
                "SELECT users.id FROM users WHERE users.username = tasks.created_by"
                ") WHERE created_by IS NOT NULL AND EXISTS ("
                "SELECT 1 FROM users WHERE users.username = tasks.created_by"
                ")"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_tasks_created_by ON tasks(created_by)"
            ))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (5, :now)"),
                {"now": datetime.now(timezone.utc)},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 6"))
        if result.scalar_one_or_none() is None:
            task_columns = await conn.execute(text("PRAGMA table_info(tasks)"))
            existing_tasks = {row[1] for row in task_columns.fetchall()}
            task_additions = {
                "started": "DATETIME",
                "updated": "DATETIME",
            }
            for name, definition in task_additions.items():
                if name not in existing_tasks:
                    await conn.execute(text(f"ALTER TABLE tasks ADD COLUMN {name} {definition}"))

            subtask_columns = await conn.execute(text("PRAGMA table_info(subtasks)"))
            existing_subtasks = {row[1] for row in subtask_columns.fetchall()}
            subtask_additions = {
                "execution_id": "VARCHAR(36)",
                "attempts": "INTEGER NOT NULL DEFAULT 0",
                "progress": "INTEGER NOT NULL DEFAULT 0",
                "message": "VARCHAR(500)",
                "error": "TEXT",
            }
            for name, definition in subtask_additions.items():
                if name not in existing_subtasks:
                    await conn.execute(text(
                        f"ALTER TABLE subtasks ADD COLUMN {name} {definition}"
                    ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_subtasks_execution_id "
                "ON subtasks(execution_id)"
            ))
            await conn.execute(text(
                "CREATE INDEX IF NOT EXISTS ix_subtasks_status ON subtasks(status)"
            ))
            now = datetime.now(timezone.utc)
            await conn.execute(
                text("UPDATE tasks SET updated = COALESCE(updated, created, :now)"),
                {"now": now},
            )
            await conn.execute(text(
                "UPDATE subtasks SET status='paused', "
                "error=COALESCE(error, 'Server restarted before task completion') "
                "WHERE status IN ('pending', 'running')"
            ))
            await conn.execute(
                text(
                    "UPDATE tasks SET status='paused', updated=:now "
                    "WHERE status IN ('pending', 'running')"
                ),
                {"now": now},
            )
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (6, :now)"),
                {"now": now},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 7"))
        if result.scalar_one_or_none() is None:
            user_columns = await conn.execute(text("PRAGMA table_info(users)"))
            existing_users = {row[1] for row in user_columns.fetchall()}
            if "disabled" not in existing_users:
                await conn.execute(
                    text("ALTER TABLE users ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0")
                )
            # Unknown and legacy non-admin roles always migrate downward. The
            # token version bump invalidates every pre-v7 access and refresh
            # token, including sessions whose embedded role is now stale.
            await conn.execute(text(
                "UPDATE users SET role=CASE WHEN role='admin' THEN 'admin' ELSE 'user' END, "
                "token_version=COALESCE(token_version, 0) + 1"
            ))
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (7, :now)"),
                {"now": datetime.now(timezone.utc)},
            )
        result = await conn.execute(text("SELECT version FROM schema_migrations WHERE version = 8"))
        if result.scalar_one_or_none() is None:
            package_columns = await conn.execute(text("PRAGMA table_info(update_packages)"))
            existing_packages = {row[1] for row in package_columns.fetchall()}
            package_additions = {
                "release_id": "VARCHAR(80)",
                "component": "VARCHAR(30) NOT NULL DEFAULT 'agent'",
                "target_os": "VARCHAR(20)",
                "target_arch": "VARCHAR(30)",
                "python_abi": "VARCHAR(20)",
                "key_id": "VARCHAR(64)",
                "manifest": "TEXT",
                "expanded_size": "INTEGER",
                "min_updater_version": "VARCHAR(50)",
                "validation_status": (
                    "VARCHAR(30) NOT NULL DEFAULT 'legacy_untrusted'"
                ),
                "validation_error": "TEXT",
                "created_by": "VARCHAR(36)",
            }
            for name, definition in package_additions.items():
                if name not in existing_packages:
                    await conn.execute(text(
                        f"ALTER TABLE update_packages ADD COLUMN {name} {definition}"
                    ))
            await conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_update_packages_release_id "
                "ON update_packages(release_id)"
            ))
            for column in (
                "target_os", "target_arch", "python_abi", "key_id",
                "validation_status", "created_by",
            ):
                await conn.execute(text(
                    f"CREATE INDEX IF NOT EXISTS ix_update_packages_{column} "
                    f"ON update_packages({column})"
                ))
            # Existing update packages predate signing and must never become
            # deployable merely because the database was migrated.
            await conn.execute(text(
                "UPDATE update_packages SET validation_status='legacy_untrusted', "
                "validation_error=COALESCE(validation_error, "
                "'Package predates signed update schema v1') "
                "WHERE release_id IS NULL OR manifest IS NULL OR key_id IS NULL"
            ))
            now = datetime.now(timezone.utc)
            # A process restart must not silently continue widening an update
            # rollout. Targets are made explicitly resumable by an administrator.
            await conn.execute(text(
                "UPDATE update_deployment_targets SET status='paused', phase='paused', "
                "error=COALESCE(error, 'Server restarted; manual retry required'), "
                "updated=:now WHERE status IN "
                "('transferring','verified','activating','health_check')"
            ), {"now": now})
            await conn.execute(text(
                "UPDATE update_deployments SET status='paused', updated=:now "
                "WHERE status IN ('queued','canary_running','rolling_out')"
            ), {"now": now})
            await conn.execute(
                text("INSERT INTO schema_migrations(version, applied_at) VALUES (8, :now)"),
                {"now": now},
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
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    backup_path = backup_dir / f"cluster_pre_v{CURRENT_SCHEMA_VERSION}_{stamp}.db"
    # SQLite's online backup API includes committed WAL pages and produces a
    # transactionally consistent copy. Copying only cluster.db could silently
    # omit committed data still present in cluster.db-wal.
    with sqlite3.connect(db_path) as source, sqlite3.connect(backup_path) as destination:
        source.backup(destination)
