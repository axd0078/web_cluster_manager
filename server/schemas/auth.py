from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    username: str
    role: Literal["admin", "user"]
    disabled: bool = False
    permissions: list[str] = Field(default_factory=list)
    step_up_expires_at: str | None = None

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=256)
    role: Literal["admin", "user"] = "user"


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=12, max_length=256)


class StepUpRequest(BaseModel):
    password: str = Field(min_length=1, max_length=256)


class StepUpResponse(BaseModel):
    expires_at: str


class UserUpdate(BaseModel):
    role: Literal["admin", "user"] | None = None
    disabled: bool | None = None
