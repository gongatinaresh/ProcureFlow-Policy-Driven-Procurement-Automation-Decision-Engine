from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.database.session import get_db
from app.models import (
    AuditEvent,
    Contract,
    ContractAllocation,
    Decision,
    DecisionLine,
    Item,
    PRLine,
    PolicyVersion,
    PurchaseHistory,
    PurchaseOrder,
    PurchaseRequirement,
    Supplier,
)
from app.services.item_matching import match_item
from app.services.policy_engine import (
    activate_due_policy_versions,
    policy_settings,
    resolve_policy,
    seed_corvo_v5,
)
from app.services.procurement_engine import (
    get_preferred_supplier_list,
    override_decision,
    process_purchase_requests,
)
from app.services.procurement_types import RequestLine
from app.utils.time import business_date, business_today

router = APIRouter()


class OverrideRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    route: str = Field(min_length=1, max_length=48)
    reason: str = Field(min_length=1, max_length=2000)


class ActionRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=2000)


class ContractReviewRequest(BaseModel):
    actor_id: str = Field(min_length=1, max_length=128)
    verified: bool
    reason: str = Field(min_length=1, max_length=2000)


class ChatRequest(BaseModel):
    requester: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=100)
    plant: str = Field(min_length=1, max_length=32)
    item: str = Field(min_length=1, max_length=255)
    quantity: Decimal = Field(gt=0)
    uom: str = Field(min_length=1, max_length=24)
    need_by_date: date
    urgency: str = Field(default="Normal", min_length=1, max_length=24)
    estimated_unit_price: Decimal | None = Field(default=None, ge=0)
    message: str = Field(min_length=1, max_length=4000)


def _decision_payload(session: Session, decision: Decision) -> dict[str, Any]:
    requirement = decision.requirement
    policy = session.get(PolicyVersion, decision.run.policy_version_id)
    supplier = session.get(Supplier, decision.supplier_id) if decision.supplier_id else None
    lines = list(
        session.scalars(
            select(PRLine)
            .join(DecisionLine, DecisionLine.pr_line_id == PRLine.id)
            .where(DecisionLine.decision_id == decision.id)
            .order_by(PRLine.created_at_source, PRLine.pr_no, PRLine.pr_line)
        )
    )
    return {
        "id": decision.id,
        "group_id": requirement.group_id,
        "item_code": requirement.item_code,
        "plant_code": requirement.plant_code,
        "requirement_date": requirement.requirement_date,
        "quantity": str(requirement.total_quantity),
        "need_by_date": requirement.need_by_date,
        "group_value_inr": str(decision.group_value_inr),
        "decision": decision.outcome,
        "route": decision.route,
        "supplier_id": decision.supplier_id,
        "supplier_name": supplier.name if supplier else None,
        "unit_price": str(decision.unit_price) if decision.unit_price is not None else None,
        "suggested_supplier_ids": decision.suggested_supplier_ids,
        "clause": decision.clause,
        "policy_version": policy.version if policy else None,
        "reason": decision.reason,
        "purchase_order": decision.purchase_order.po_number if decision.purchase_order else None,
        "status": requirement.status,
        "overridden": decision.overridden,
        "lines": [
            {
                "pr_no": line.pr_no,
                "pr_line": line.pr_line,
                "description": line.description,
                "quantity": str(line.quantity),
                "uom": line.uom,
                "plant": line.plant_code,
                "created_at": line.created_at_source,
                "need_by_date": line.need_by_date,
                "urgency": line.urgency,
            }
            for line in lines
        ],
    }


@router.get("/dashboard")
def dashboard(session: Session = Depends(get_db)) -> dict[str, Any]:
    seed_corvo_v5(session)
    active_policy = activate_due_policy_versions(session, business_today())
    decisions = list(session.scalars(select(Decision)))
    if active_policy is None:
        active_policy = session.scalar(
            select(PolicyVersion)
            .where(PolicyVersion.status == "active")
            .order_by(PolicyVersion.effective_from.desc(), PolicyVersion.id.desc())
            .limit(1)
        )
    session.commit()
    return {
        "purchase_requests": session.scalar(select(func.count()).select_from(PRLine)) or 0,
        "po_decisions": sum(decision.outcome == "PO" for decision in decisions),
        "sourcing_requests": sum(decision.outcome == "SOURCING_REQUEST" for decision in decisions),
        "pending_buyer_actions": sum(
            decision.requirement.status in {"pending", "decided"}
            and decision.outcome != "NOT_PROCESSED"
            for decision in decisions
        ),
        "active_policy_version": active_policy.version if active_policy else None,
        "recent_decisions": [
            _decision_payload(session, decision)
            for decision in sorted(decisions, key=lambda row: row.created_at, reverse=True)[:8]
        ],
    }


@router.get("/buyer/requirements")
def buyer_requirements(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    decisions = session.scalars(
        select(Decision)
        .join(PurchaseRequirement, Decision.requirement_id == PurchaseRequirement.id)
        .where(PurchaseRequirement.status.in_(("pending", "decided")))
        .where(Decision.outcome != "NOT_PROCESSED")
        .order_by(PurchaseRequirement.requirement_date, PurchaseRequirement.first_created_at)
    )
    return [_decision_payload(session, decision) for decision in decisions]


@router.post("/buyer/decisions/{decision_id}/approve")
def approve_buyer_decision(
    decision_id: int,
    request: ActionRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    decision = session.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision does not exist.")
    if decision.requirement.status not in {"pending", "decided"}:
        raise HTTPException(status_code=409, detail="Decision is not pending buyer action.")
    if decision.purchase_order is not None:
        decision.purchase_order.status = "approved"
        decision.purchase_order.approved_by = request.actor_id
        decision.purchase_order.approved_at = datetime.now(timezone.utc)
        allocations = session.scalars(
            select(ContractAllocation).where(
                ContractAllocation.requirement_id == decision.requirement_id,
                ContractAllocation.status == "reserved",
            )
        )
        for allocation in allocations:
            allocation.status = "committed"
    decision.requirement.status = "approved"
    session.add(
        AuditEvent(
            actor_id=request.actor_id,
            action="buyer_decision_approved",
            entity_type="decision",
            entity_id=str(decision.id),
            reason=request.reason,
            after_state={"route": decision.route, "purchase_order_id": decision.purchase_order_id},
        )
    )
    session.commit()
    return _decision_payload(session, decision)


@router.post("/buyer/decisions/{decision_id}/override")
def override_buyer_decision(
    decision_id: int,
    request: OverrideRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    allowed_routes = {
        "CONTRACT_PO",
        "EMERGENCY_PO",
        "SPOT_PO",
        "RFQ",
        "RFP",
        "CAPEX_SOURCING",
        "NEW_ITEM_SOURCING",
    }
    if request.route not in allowed_routes:
        raise HTTPException(status_code=422, detail="Unsupported procurement route.")
    decision = session.get(Decision, decision_id)
    if decision is None:
        raise HTTPException(status_code=404, detail="Decision does not exist.")
    if decision.requirement.status not in {"pending", "decided"}:
        raise HTTPException(status_code=409, detail="Only pending decisions can be overridden.")
    new_outcome = "PO" if request.route.endswith("_PO") else "SOURCING_REQUEST"
    if new_outcome != decision.outcome:
        raise HTTPException(
            status_code=409,
            detail="Route overrides must remain within the existing decision type.",
        )
    if decision.route == "CONTRACT_PO" and request.route != "CONTRACT_PO":
        raise HTTPException(
            status_code=409,
            detail="A contract-backed decision cannot be rerouted without re-evaluating its valuation and ceiling.",
        )
    try:
        decision = override_decision(
            session,
            decision_id,
            route=request.route,
            reason=request.reason,
            actor_id=request.actor_id,
        )
        if decision.purchase_order is not None:
            decision.purchase_order.route = request.route
        decision.requirement.status = "pending"
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error
    return _decision_payload(session, decision)


@router.get("/suppliers")
def list_suppliers(
    as_of: date,
    search: str | None = None,
    session: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    seed_corvo_v5(session)
    statement = select(Supplier).order_by(Supplier.supplier_id)
    if search:
        pattern = f"%{search.strip()}%"
        statement = statement.where(
            Supplier.name.ilike(pattern) | Supplier.supplier_id.ilike(pattern)
        )
    suppliers = list(session.scalars(statement))
    result: list[dict[str, Any]] = []
    for supplier in suppliers:
        purchase_count = session.scalar(
            select(func.count()).select_from(PurchaseHistory).where(
                PurchaseHistory.supplier_id == supplier.supplier_id,
                PurchaseHistory.po_date <= as_of,
            )
        )
        recent_history = list(
            session.scalars(
                select(PurchaseHistory)
                .where(
                    PurchaseHistory.supplier_id == supplier.supplier_id,
                    PurchaseHistory.po_date <= as_of,
                )
                .order_by(PurchaseHistory.po_date.desc(), PurchaseHistory.po_number.desc())
                .limit(5)
            )
        )
        supplier_contracts = list(
            session.scalars(
                select(Contract)
                .where(Contract.supplier_id == supplier.supplier_id)
                .order_by(Contract.end_date.desc())
            )
        )
        ranking: list[dict[str, Any]] = []
        if supplier.status == "Active":
            for item_code in sorted(
                {
                    row.item_code
                    for row in session.scalars(
                        select(PurchaseHistory).where(
                            PurchaseHistory.supplier_id == supplier.supplier_id,
                            PurchaseHistory.po_date <= as_of,
                        )
                    )
                }
            ):
                for rank, entry in enumerate(
                    get_preferred_supplier_list(session, item_code, as_of), start=1
                ):
                    if entry["supplier"].supplier_id == supplier.supplier_id:
                        ranking.append(
                            {
                                "item_code": item_code,
                                "rank": rank,
                                "reference_price": str(entry["reference_price"]),
                            }
                        )
        result.append(
            {
                "supplier_id": supplier.supplier_id,
                "name": supplier.name,
                "city": supplier.city,
                "status": supplier.status,
                "categories": supplier.categories,
                "on_time_pct": str(supplier.on_time_pct),
                "quality_rating": str(supplier.quality_rating),
                "purchase_count": purchase_count or 0,
                "recent_purchase_history": [
                    {
                        "po_number": row.po_number,
                        "po_date": row.po_date,
                        "item_code": row.item_code,
                        "quantity": str(row.quantity),
                        "unit_price": str(row.unit_price),
                        "delivered_on_time": row.delivered_on_time,
                    }
                    for row in recent_history
                ],
                "contracts": [
                    {
                        "contract_id": contract.contract_id,
                        "end_date": contract.end_date,
                        "verified": contract.verified,
                    }
                    for contract in supplier_contracts
                ],
                "preferred_ranking": ranking,
            }
        )
    session.commit()
    return result


@router.get("/contracts")
def list_contracts(session: Session = Depends(get_db)) -> list[dict[str, Any]]:
    contracts = session.scalars(select(Contract).order_by(Contract.end_date.desc()))
    result: list[dict[str, Any]] = []
    for contract in contracts:
        allocated = sum(
            (
                Decimal(amount)
                for amount in session.scalars(
                    select(ContractAllocation.amount).where(
                        ContractAllocation.contract_id == contract.id,
                        ContractAllocation.status.in_(("reserved", "committed")),
                    )
                )
            ),
            Decimal("0"),
        )
        supplier = session.get(Supplier, contract.supplier_id)
        result.append(
            {
                "id": contract.id,
                "contract_id": contract.contract_id,
                "supplier_id": contract.supplier_id,
                "supplier_name": supplier.name if supplier else None,
                "start_date": contract.start_date,
                "end_date": contract.end_date,
                "value_cap": str(contract.value_cap),
                "baseline_consumed_value": str(contract.baseline_consumed_value),
                "allocated_value": str(allocated),
                "remaining_value": str(
                    max(
                        Decimal("0"),
                        Decimal(contract.value_cap)
                        - Decimal(contract.baseline_consumed_value)
                        - allocated,
                    )
                ),
                "source_type": contract.source_type,
                "source_reference": contract.source_reference,
                "verified": contract.verified,
                "review_status": contract.review_status,
                "lines": [
                    {
                        "item_code": line.item_code,
                        "description": line.description,
                        "uom": line.uom,
                        "rate": str(line.rate),
                    }
                    for line in contract.lines
                ],
            }
        )
    return result


@router.post("/contracts/{contract_id}/review")
def review_contract(
    contract_id: int,
    request: ContractReviewRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    contract = session.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="Contract does not exist.")
    before = {"verified": contract.verified, "review_status": contract.review_status}
    contract.verified = request.verified
    contract.review_status = "verified" if request.verified else "rejected"
    session.add(
        AuditEvent(
            actor_id=request.actor_id,
            action="contract_reviewed",
            entity_type="contract",
            entity_id=str(contract.id),
            reason=request.reason,
            before_state=before,
            after_state={
                "verified": contract.verified,
                "review_status": contract.review_status,
            },
        )
    )
    session.commit()
    return {
        "id": contract.id,
        "contract_id": contract.contract_id,
        "verified": contract.verified,
        "review_status": contract.review_status,
    }


@router.post("/chat/requests")
def create_chat_request(
    request: ChatRequest,
    session: Session = Depends(get_db),
) -> dict[str, Any]:
    policy = resolve_policy(session, None, business_today())
    settings = policy_settings(policy)
    request_number = f"CHAT-{uuid4().hex[:12].upper()}"
    match = match_item(
        RequestLine(
            pr_no=request_number,
            pr_line=1,
            created_at=datetime.now(timezone.utc),
            requester=request.requester,
            department=request.department,
            plant_code=request.plant,
            supplied_item_code=request.item if session.get(Item, request.item) else None,
            description=request.item,
            quantity=request.quantity,
            uom=request.uom,
            estimated_unit_price=request.estimated_unit_price,
            need_by_date=request.need_by_date,
            urgency=request.urgency,
        ),
        list(session.scalars(select(Item).order_by(Item.item_code))),
        settings["item_matching"],
    )
    line = RequestLine(
        pr_no=request_number,
        pr_line=1,
        created_at=datetime.now(timezone.utc),
        requester=request.requester,
        department=request.department,
        plant_code=request.plant,
        supplied_item_code=match.item.item_code if match.item else None,
        description=request.message,
        quantity=request.quantity,
        uom=request.uom,
        estimated_unit_price=request.estimated_unit_price,
        need_by_date=request.need_by_date,
        urgency=request.urgency,
    )
    try:
        result = process_purchase_requests(
            session, [line], as_of=business_date(line.created_at)
        )
        session.commit()
    except ValueError as error:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    requirement = result.requirements[0]
    return {
        "pr_no": line.pr_no,
        "pr_line": line.pr_line,
        "resolved_item_code": requirement.item.item_code if requirement.item else "NEW",
        "decision": requirement.decision,
        "route": requirement.route,
        "group_id": requirement.group_id,
        "po_id": requirement.purchase_order_group,
        "supplier_id": requirement.supplier.supplier_id if requirement.supplier else None,
        "suggested_supplier_ids": [
            supplier.supplier_id for supplier in requirement.suggested_suppliers
        ],
        "unit_price": str(requirement.unit_price) if requirement.unit_price is not None else None,
        "group_value_inr": str(requirement.value) if requirement.value is not None else None,
        "policy_version": result.policy_version,
        "clause": requirement.clause,
        "reason": requirement.reason,
        "match_candidates": [
            {"item_code": code, "score": str(score)}
            for code, score in (match.candidates if match else [])
        ],
    }
