#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""节点 ORM 模型"""

import uuid
from datetime import datetime, timezone
from sqlalchemy import String, Float, DateTime, JSON
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


class Node(Base):
    __tablename__ = "nodes"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    ip: Mapped[str] = mapped_column(String(45), unique=True, nullable=False, index=True)
    hostname: Mapped[str] = mapped_column(String(255), default="")
    os: Mapped[str] = mapped_column(String(100), default="")
    status: Mapped[str] = mapped_column(String(20), default="online")  # online/offline
    version: Mapped[str] = mapped_column(String(50), default="1.0.0")
    tags: Mapped[dict] = mapped_column(JSON, default=dict)
    metadata_: Mapped[dict] = mapped_column("metadata", JSON, default=dict)
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    registered: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<Node {self.ip} ({self.status})>"
