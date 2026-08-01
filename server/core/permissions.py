from __future__ import annotations

from collections.abc import Iterable


USER_PERMISSIONS = frozenset({
    "cluster.read",
    "alerts.resolve",
    "tasks.low",
    "files.write_sandbox",
    "terminal.low",
    "history.own",
})

ADMIN_PERMISSIONS = frozenset({
    *USER_PERMISSIONS,
    "history.all",
    "audit.read",
    "users.read",
    "settings.read",
    "brokers.read",
    "updates.read",
})

STEP_UP_PERMISSIONS = frozenset({
    "tasks.high",
    "terminal.admin",
    "files.overwrite",
    "containers.control",
    "containers.logs",
    "nodes.manage",
    "groups.manage",
    "agents.manage",
    "brokers.manage",
    "users.manage",
    "alerts.manage",
    "updates.manage",
    "settings.write",
    "audit.export",
})

VALID_ROLES = frozenset({"user", "admin"})


def normalize_role(role: str | None) -> str:
    """Fail closed when a stale or unknown role reaches application code."""
    return "admin" if role == "admin" else "user"


def permissions_for(role: str | None, *, step_up: bool = False) -> frozenset[str]:
    normalized = normalize_role(role)
    permissions = ADMIN_PERMISSIONS if normalized == "admin" else USER_PERMISSIONS
    if normalized == "admin" and step_up:
        return permissions | STEP_UP_PERMISSIONS
    return permissions


def has_permissions(
    role: str | None,
    required: str | Iterable[str],
    *,
    step_up: bool = False,
) -> bool:
    expected = {required} if isinstance(required, str) else set(required)
    return expected.issubset(permissions_for(role, step_up=step_up))
