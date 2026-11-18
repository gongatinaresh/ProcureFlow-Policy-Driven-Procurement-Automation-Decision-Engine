from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PolicyUploadResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    version: str
    effective_from: date
    status: str
    source_reference: str | None
    source_sha256: str | None
    extracted_text: str
    policy_document: dict[str, Any]
    change_summary: str | None
    extraction_status: str


class PolicyValidateRequest(BaseModel):
    policy_document: dict[str, Any]
    actor_id: str = Field(min_length=1, max_length=128)


class PolicyApprovalRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class PolicyRejectionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class PolicyRuleDiff(BaseModel):
    path: str
    before: Any
    after: Any


class PolicyDiffResponse(BaseModel):
    policy_id: int
    version: str
    base_version: str
    status: str
    effective_from: date
    summary: str
    changes: list[PolicyRuleDiff]


class PolicyListItem(BaseModel):
    id: int
    version: str
    effective_from: date
    status: str
    source_reference: str | None
    change_summary: str | None
    created_by: str | None
    approved_by: str | None
    approved_at: datetime | None
    parent_policy_version_id: int | None
