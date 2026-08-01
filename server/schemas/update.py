from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class UpdateTargets(BaseModel):
    package_id: str
    target_node_ids: list[str] = Field(default_factory=list, max_length=32)
    target_group_ids: list[str] = Field(default_factory=list, max_length=32)
    all_online: bool = False

    @field_validator("package_id")
    @classmethod
    def validate_package_id(cls, value: str) -> str:
        if not value or len(value) > 64:
            raise ValueError("更新包 ID 无效")
        return value

    @field_validator("target_node_ids", "target_group_ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("目标 ID 无效")
        return list(dict.fromkeys(value))


class UpdateDeploymentCreate(UpdateTargets):
    canary_node_ids: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("canary_node_ids")
    @classmethod
    def validate_canary_ids(cls, value: list[str]) -> list[str]:
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("canary 节点 ID 无效")
        return list(dict.fromkeys(value))

    @model_validator(mode="after")
    def validate_canary(self):
        if not self.canary_node_ids:
            raise ValueError("必须选择至少一个 canary 节点")
        return self


class UpdateRetry(BaseModel):
    target_node_ids: list[str] | None = Field(default=None, max_length=32)

    @field_validator("target_node_ids")
    @classmethod
    def validate_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("目标 ID 无效")
        return list(dict.fromkeys(value))


class UpdateRollback(BaseModel):
    target_node_ids: list[str] | None = Field(default=None, max_length=32)

    @field_validator("target_node_ids")
    @classmethod
    def validate_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        if any(not item or len(item) > 64 for item in value):
            raise ValueError("目标 ID 无效")
        return list(dict.fromkeys(value))
