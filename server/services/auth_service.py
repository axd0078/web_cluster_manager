from __future__ import annotations

from core.security import hash_password
from models.user import User


class AuthService:
    """Explicit administrator bootstrap helper; never supplies a default password."""

    @staticmethod
    async def ensure_admin(db, username: str, password: str):
        from sqlalchemy import select
        if len(password) < 12:
            raise ValueError("administrator bootstrap password must be at least 12 characters")
        result = await db.execute(select(User).where(User.username == username))
        if result.scalar_one_or_none() is None:
            user = User(username=username, password=hash_password(password), role="admin")
            db.add(user)
            return user
        return None
