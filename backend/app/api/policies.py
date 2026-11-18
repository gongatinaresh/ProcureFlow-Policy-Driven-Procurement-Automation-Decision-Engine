import json
from datetime import date
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import PolicyVersion
from app.schemas.policy_management import (
    PolicyApprovalRequest,
    PolicyDiffResponse,
    PolicyListItem,
    PolicyRejectionRequest,
    PolicyUploadResponse,
    PolicyValidateRequest,
)
from app.services.policy_engine import seed_corvo_v5
from app.services.policy_management import (
    MAX_POLICY_PDF_BYTES,
    PolicyManagementError,
    activate_due_policies,
    approve_policy,
    create_policy_draft,
    policy_diff,
    reject_policy,
    validate_policy_draft,
)
from app.utils.time import business_today

router = APIRouter(prefix="/policies")


def _policy_or_404(session: Session, policy_id: int) -> PolicyVersion:
    policy = session.get(PolicyVersion, policy_id)
    if policy is None:
        raise HTTPException(status_code=404, detail="Policy version does not exist.")
    return policy


@router.post("/upload", response_model=PolicyUploadResponse, status_code=201)
def upload_policy(
    file: UploadFile = File(...),
    effective_from: date = Form(...),
    actor_id: str = Form(..., min_length=1, max_length=128),
    policy_document: str | None = Form(default=None),
    session: Session = Depends(get_db),
) -> PolicyUploadResponse:
    structured_document: dict[str, Any] | None = None
    if policy_document:
        try:
            parsed = json.loads(policy_document)
        except json.JSONDecodeError as error:
            raise HTTPException(
                status_code=422,
                detail=f"Structured policy JSON is invalid: {error.msg}.",
            ) from error
        if not isinstance(parsed, dict):
            raise HTTPException(status_code=422, detail="Structured policy must be a JSON object.")
        structured_document = parsed
    content = file.file.read(MAX_POLICY_PDF_BYTES + 1)
    try:
        policy = create_policy_draft(
            session,
            filename=file.filename or "policy.pdf",
            content=content,
            effective_from=effective_from,
            actor_id=actor_id,
            policy_document=structured_document,
        )
        session.commit()
    except PolicyManagementError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return PolicyUploadResponse(
        id=policy.id,
        version=policy.version,
        effective_from=policy.effective_from,
        status=policy.status,
        source_reference=policy.source_reference,
        source_sha256=policy.source_sha256,
        extracted_text=policy.extracted_text or "",
        policy_document=policy.policy_document or {},
        change_summary=policy.change_summary,
        extraction_status=(
            "text_extracted_manual_rule_review_required"
            if policy.extracted_text
            else "no_selectable_text_manual_structured_entry_required"
        ),
    )


@router.post("/{policy_id}/validate")
def validate_policy(
    policy_id: int,
    request: PolicyValidateRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = _policy_or_404(session, policy_id)
    try:
        policy, changes = validate_policy_draft(
            session, policy, request.policy_document, request.actor_id
        )
        session.commit()
    except PolicyManagementError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "id": policy.id,
        "version": policy.version,
        "status": policy.status,
        "valid": True,
        "rule_count": len(policy.policy_document["settings"]["decision_rules"]),
        "change_summary": policy.change_summary,
        "change_count": len(changes),
    }


@router.get("/{policy_id}/diff", response_model=PolicyDiffResponse)
def get_policy_diff(
    policy_id: int,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = _policy_or_404(session, policy_id)
    try:
        return policy_diff(session, policy)
    except PolicyManagementError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error


@router.post("/{policy_id}/approve")
def approve_policy_version(
    policy_id: int,
    request: PolicyApprovalRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = _policy_or_404(session, policy_id)
    try:
        policy = approve_policy(
            session,
            policy,
            actor_id=request.actor_id,
            reason=request.reason,
        )
        session.commit()
    except PolicyManagementError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "id": policy.id,
        "version": policy.version,
        "effective_from": policy.effective_from,
        "status": policy.status,
        "approved_by": policy.approved_by,
        "approved_at": policy.approved_at,
    }


@router.post("/{policy_id}/reject")
def reject_policy_version(
    policy_id: int,
    request: PolicyRejectionRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = _policy_or_404(session, policy_id)
    try:
        policy = reject_policy(
            session,
            policy,
            actor_id=request.actor_id,
            reason=request.reason,
        )
        session.commit()
    except PolicyManagementError as error:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {"id": policy.id, "version": policy.version, "status": policy.status}


@router.get("", response_model=list[PolicyListItem])
def list_policies(session: Session = Depends(get_db)) -> list[PolicyVersion]:
    seed_corvo_v5(session)
    session.commit()
    return list(
        session.scalars(
            select(PolicyVersion).order_by(
                PolicyVersion.effective_from.desc(),
                PolicyVersion.id.desc(),
            )
        )
    )


@router.get("/active")
def get_active_policy(session: Session = Depends(get_db)) -> dict[str, Any]:
    try:
        policy = activate_due_policies(session, business_today())
    except PolicyManagementError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    if policy is None:
        raise HTTPException(status_code=404, detail="No approved policy is effective today.")
    session.commit()
    return {
        "id": policy.id,
        "version": policy.version,
        "effective_from": policy.effective_from,
        "status": policy.status,
        "policy_document": policy.policy_document,
        "change_summary": policy.change_summary,
    }
