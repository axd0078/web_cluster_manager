#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""认证相关 Pydantic schemas"""

from pydantic import BaseModel, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserInfo(BaseModel):
    id: str
    username: str
    role: str

    model_config = {"from_attributes": True}
