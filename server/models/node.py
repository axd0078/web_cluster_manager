from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database import Base


def _utcnow():
    return datetime.now(timezone.utc)


def _pk():
    return str(uuid.uuid4())


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    ip: Mapped[str] = mapped_column(String(45), nullable=False, index=True)
    agent_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    os: Mapped[str | None] = mapped_column(String(255))
    platform: Mapped[str] = mapped_column(String(30), default="unknown")
    capabilities: Mapped[str] = mapped_column(Text, default="{}")
    credential_state: Mapped[str] = mapped_column(String(30), default="reenrollment_required")
    status: Mapped[str] = mapped_column(String(20), default="offline", index=True)
    version: Mapped[str | None] = mapped_column(String(20))
    tags: Mapped[str] = mapped_column(Text, default="[]")
    extra_data: Mapped[str] = mapped_column("extra_data", Text, default="{}")
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    metrics: Mapped[list[Metric]] = relationship(back_populates="node", cascade="all, delete-orphan")
    groups: Mapped[list[GroupNode]] = relationship(back_populates="node", cascade="all, delete-orphan")
    containers: Mapped[list[ContainerResource]] = relationship(back_populates="host", cascade="all, delete-orphan")
    credential: Mapped[AgentCredential | None] = relationship(back_populates="node", cascade="all, delete-orphan", uselist=False)


class EnrollmentToken(Base):
    __tablename__ = "enrollment_tokens"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    label: Mapped[str | None] = mapped_column(String(200))
    created_by: Mapped[str | None] = mapped_column(String(36), index=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AgentCredential(Base):
    __tablename__ = "agent_credentials"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), unique=True, index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    node: Mapped[Node] = relationship(back_populates="credential")


class ContainerResource(Base):
    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("host_node_id", "runtime_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    host_node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    runtime_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    image: Mapped[str | None] = mapped_column(String(500))
    state: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    status: Mapped[str | None] = mapped_column(String(500))
    labels: Mapped[str] = mapped_column(Text, default="{}")
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    mem_percent: Mapped[float | None] = mapped_column(Float)
    mem_usage: Mapped[str | None] = mapped_column(String(100))
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)

    host: Mapped[Node] = relationship(back_populates="containers")
    metrics: Mapped[list[ContainerMetric]] = relationship(back_populates="container", cascade="all, delete-orphan")


class ContainerMetric(Base):
    __tablename__ = "container_metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    container_id: Mapped[str] = mapped_column(ForeignKey("containers.id", ondelete="CASCADE"), index=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    mem_percent: Mapped[float | None] = mapped_column(Float)
    mem_usage: Mapped[str | None] = mapped_column(String(100))

    container: Mapped[ContainerResource] = relationship(back_populates="metrics")


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color: Mapped[str | None] = mapped_column(String(20))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    nodes: Mapped[list[GroupNode]] = relationship(back_populates="group", cascade="all, delete-orphan")


class GroupNode(Base):
    __tablename__ = "group_nodes"
    __table_args__ = (UniqueConstraint("group_id", "node_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    group_id: Mapped[str] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"))
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"))

    group: Mapped[Group] = relationship(back_populates="nodes")
    node: Mapped[Node] = relationship(back_populates="groups")


class Metric(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    time: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
    node_id: Mapped[str] = mapped_column(ForeignKey("nodes.id", ondelete="CASCADE"), index=True)
    cpu_percent: Mapped[float | None] = mapped_column(Float)
    mem_percent: Mapped[float | None] = mapped_column(Float)
    mem_used: Mapped[int | None] = mapped_column(Integer)
    mem_total: Mapped[int | None] = mapped_column(Integer)
    disk_percent: Mapped[float | None] = mapped_column(Float)
    disk_used: Mapped[int | None] = mapped_column(Integer)
    disk_total: Mapped[int | None] = mapped_column(Integer)

    node: Mapped[Node] = relationship(back_populates="metrics")


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    node_id: Mapped[str | None] = mapped_column(String(36), index=True)
    rule_id: Mapped[str | None] = mapped_column(String(36))
    severity: Mapped[str] = mapped_column(String(20), default="warning")
    message: Mapped[str] = mapped_column(Text, nullable=False)
    resolved: Mapped[bool] = mapped_column(default=False)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    type: Mapped[str] = mapped_column(String(30), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String(255))
    params: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    created_by: Mapped[str | None] = mapped_column(String(100))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subtasks: Mapped[list[Subtask]] = relationship(back_populates="task", cascade="all, delete-orphan")


class Subtask(Base):
    __tablename__ = "subtasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), index=True)
    node_id: Mapped[str] = mapped_column(String(36), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    execution_id: Mapped[str | None] = mapped_column(String(36), unique=True, index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(String(500))
    error: Mapped[str | None] = mapped_column(Text)
    result: Mapped[str | None] = mapped_column(Text)
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    task: Mapped[Task] = relationship(back_populates="subtasks")


class FileTransfer(Base):
    __tablename__ = "file_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    size: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(36))
    targets: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="pending")
    sha256: Mapped[str | None] = mapped_column(String(64))
    dest_path: Mapped[str | None] = mapped_column(String(500))
    overwrite: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[str | None] = mapped_column(String(36), index=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    target_results: Mapped[list[FileTransferTarget]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan",
    )


class FileUpload(Base):
    __tablename__ = "file_uploads"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_name: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    received_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="uploading", index=True)
    created_by: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    completed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class FileTransferTarget(Base):
    __tablename__ = "file_transfer_targets"
    __table_args__ = (UniqueConstraint("transfer_id", "node_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    transfer_id: Mapped[str] = mapped_column(
        ForeignKey("file_transfers.id", ondelete="CASCADE"), index=True,
    )
    node_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    bytes_sent: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transfer: Mapped[FileTransfer] = relationship(back_populates="target_results")


class UpdatePackage(Base):
    __tablename__ = "update_packages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    filename: Mapped[str | None] = mapped_column(String(500))
    size: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    metric: Mapped[str] = mapped_column(String(50), nullable=False)
    condition: Mapped[str] = mapped_column(String(10), nullable=False, default=">")
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    duration: Mapped[int] = mapped_column(Integer, default=60)
    enabled: Mapped[bool] = mapped_column(default=True)
    channels: Mapped[str] = mapped_column(Text, default="[]")


class AuditLog(Base):
    __tablename__ = "audit_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_pk)
    user_id: Mapped[str | None] = mapped_column(String(36), index=True)
    action: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    resource: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(45))
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, index=True)
