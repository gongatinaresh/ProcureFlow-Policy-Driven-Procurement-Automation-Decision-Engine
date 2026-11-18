from datetime import date, datetime
from typing import Any

from pydantic import Field

from app.schemas.common import ORMModel


class PolicyRuleCreate(ORMModel):
    order: int = Field(gt=0)
    clause: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    condition: dict[str, Any]
    outcome: dict[str, Any]


class PolicyRuleResponse(PolicyRuleCreate):
    id: int
    policy_version_id: int


class PolicyVersionCreate(ORMModel):
    version: str = Field(min_length=1, max_length=64)
    effective_from: date
    source_reference: str | None = Field(default=None, max_length=512)
    change_summary: str | None = Field(default=None, max_length=2000)
    rules: list[PolicyRuleCreate] = Field(default_factory=list)


class PolicyVersionResponse(ORMModel):
    id: int
    version: str
    effective_from: date
    status: str
    source_reference: str | None
    change_summary: str | None
    created_by: str | None
    approved_by: str | None
    approved_at: datetime | None
