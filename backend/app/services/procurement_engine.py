import hashlib
import json
from collections.abc import Iterable
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    AuditEvent,
    Contract,
    ContractAllocation,
    ContractLine,
    Decision,
    DecisionLine,
    DecisionRun,
    Item,
    Plant,
    PolicyRule,
    PolicyVersion,
    PRLine,
    PurchaseHistory,
    PurchaseOrder,
    PurchaseOrderLine,
    PurchaseRequirement,
    Supplier,
)
from app.services.clubbing import club_lines
from app.services.contract_selection import (
    applicable_contracts,
    contract_for_fallback_valuation,
    select_contract_with_capacity,
)
from app.services.decision_generation import explain_override, generate_decision
from app.services.item_matching import match_item
from app.services.policy_engine import (
    PolicyEvaluationError,
    ordered_rules,
    policy_settings,
    resolve_policy,
)
from app.services.po_consolidation import consolidate_purchase_orders
from app.services.procurement_types import (
    ItemMatch,
    ProcurementRunResult,
    RequestLine,
    Requirement,
)
from app.services.supplier_ranking import rank_preferred_suppliers
from app.services.valuation import recent_purchase_history, value_requirement
from app.utils.time import business_date, normalize_datetime


def _to_request_line(line: RequestLine | PRLine) -> RequestLine:
    if isinstance(line, RequestLine):
        return RequestLine(
            **{
                **line.__dict__,
                "created_at": normalize_datetime(line.created_at),
            }
        )
    return RequestLine(
        pr_no=line.pr_no,
        pr_line=line.pr_line,
        created_at=normalize_datetime(line.created_at_source, naive_is_business=False),
        requester=line.requester,
        department=line.department,
        plant_code=line.plant_code,
        supplied_item_code=line.supplied_item_code,
        description=line.description,
        quantity=Decimal(line.quantity),
        uom=line.uom,
        estimated_unit_price=(
            Decimal(line.estimated_unit_price)
            if line.estimated_unit_price is not None
            else None
        ),
        need_by_date=line.need_by_date,
        urgency=line.urgency,
        database_id=line.id,
    )


def _input_digest(lines: list[RequestLine], policy: PolicyVersion) -> str:
    rows = [
        {
            "pr_no": line.pr_no,
            "pr_line": line.pr_line,
            "created_at": line.created_at.isoformat(),
            "requester": line.requester,
            "department": line.department,
            "plant_code": line.plant_code,
            "item_code": line.supplied_item_code,
            "description": line.description,
            "quantity": str(line.quantity),
            "uom": line.uom,
            "estimated_unit_price": (
                str(line.estimated_unit_price) if line.estimated_unit_price is not None else None
            ),
            "need_by_date": line.need_by_date.isoformat(),
            "urgency": line.urgency,
        }
        for line in sorted(
            lines,
            key=lambda row: (row.created_at, row.pr_no, row.pr_line),
        )
    ]
    payload = json.dumps(
        {"policy_version": policy.version, "lines": rows},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_unique_lines(lines: list[RequestLine]) -> None:
    seen: set[tuple[str, int]] = set()
    duplicates: list[tuple[str, int]] = []
    for line in lines:
        if line.key in seen:
            duplicates.append(line.key)
        seen.add(line.key)
    if duplicates:
        raise ValueError(f"Duplicate PR line keys in one evaluation: {duplicates!r}")


def _existing_pr_line(session: Session, line: RequestLine) -> PRLine:
    existing = session.scalar(
        select(PRLine).where(PRLine.pr_no == line.pr_no, PRLine.pr_line == line.pr_line)
    )
    if existing is not None:
        existing_created = normalize_datetime(
            existing.created_at_source, naive_is_business=False
        )
        fields_match = (
            existing_created == line.created_at
            and existing.requester == line.requester
            and existing.department == line.department
            and existing.plant_code == line.plant_code
            and existing.supplied_item_code == line.supplied_item_code
            and existing.description == line.description
            and Decimal(existing.quantity) == line.quantity
            and existing.uom == line.uom
            and (
                Decimal(existing.estimated_unit_price)
                if existing.estimated_unit_price is not None
                else None
            )
            == line.estimated_unit_price
            and existing.need_by_date == line.need_by_date
            and existing.urgency == line.urgency
        )
        if not fields_match:
            raise ValueError(
                f"PR line {line.pr_no}/{line.pr_line} already exists with different source data."
            )
        return existing

    known_plant = session.get(Plant, line.plant_code)
    record = PRLine(
        pr_no=line.pr_no,
        pr_line=line.pr_line,
        created_at_source=line.created_at,
        requester=line.requester,
        department=line.department,
        plant_code=line.plant_code,
        matched_plant_code=known_plant.plant_code if known_plant else None,
        supplied_item_code=line.supplied_item_code,
        description=line.description,
        quantity=line.quantity,
        uom=line.uom,
        estimated_unit_price=line.estimated_unit_price,
        need_by_date=line.need_by_date,
        urgency=line.urgency,
    )
    session.add(record)
    session.flush()
    return record


def _existing_run_result(
    session: Session, run: DecisionRun, policy_version: str
) -> ProcurementRunResult:
    decisions = list(
        session.scalars(
            select(Decision)
            .where(Decision.decision_run_id == run.id)
            .order_by(Decision.id)
        )
    )
    requirements: list[Requirement] = []
    groups: dict[str, list[Requirement]] = {}
    for decision in decisions:
        model = decision.requirement
        lines = [
            _to_request_line(line)
            for line in session.scalars(
                select(PRLine)
                .join(DecisionLine, DecisionLine.pr_line_id == PRLine.id)
                .where(DecisionLine.decision_id == decision.id)
                .order_by(PRLine.created_at_source, PRLine.pr_no, PRLine.pr_line)
            )
        ]
        suppliers = list(
            session.scalars(
                select(Supplier)
                .join(
                    PurchaseOrder,
                    PurchaseOrder.supplier_id == Supplier.supplier_id,
                )
                .where(PurchaseOrder.id == decision.purchase_order_id)
            )
        ) if decision.purchase_order_id else []
        requirement = Requirement(
            ordinal=len(requirements) + 1,
            lines=lines,
            item=session.get(Item, model.item_code) if model.item_code else None,
            plant_code=model.plant_code,
            match=None,
            requirement_date=model.requirement_date,
            first_created_at=normalize_datetime(
                model.first_created_at, naive_is_business=False
            ),
            need_by_date=model.need_by_date,
            quantity=Decimal(model.total_quantity),
            is_emergency=model.is_emergency,
            group_id=model.group_id,
            unit_price=Decimal(decision.unit_price) if decision.unit_price is not None else None,
            value=Decimal(decision.group_value_inr),
            price_source=decision.price_source,
            decision=decision.outcome,
            route=decision.route,
            clause=decision.clause,
            supplier=suppliers[0] if suppliers else None,
            contract=session.get(Contract, decision.contract_id) if decision.contract_id else None,
            reason=decision.reason,
            evidence=decision.evidence,
            purchase_order_group=(
                decision.purchase_order.po_number if decision.purchase_order else None
            ),
            non_processed_reason=(
                decision.reason if decision.outcome == "NOT_PROCESSED" else None
            ),
        )
        requirement.suggested_suppliers = [
            supplier
            for supplier_id in decision.suggested_supplier_ids
            if (supplier := session.get(Supplier, supplier_id)) is not None
        ]
        requirements.append(requirement)
        if requirement.purchase_order_group:
            groups.setdefault(requirement.purchase_order_group, []).append(requirement)
    return ProcurementRunResult(run.run_id, policy_version, requirements, groups)


def _load_runtime_data(session: Session, settings: dict[str, Any]) -> dict[str, Any]:
    items = list(session.scalars(select(Item).order_by(Item.item_code)))
    suppliers = list(session.scalars(select(Supplier).order_by(Supplier.supplier_id)))
    plants = list(session.scalars(select(Plant).order_by(Plant.plant_code)))
    history = list(
        session.scalars(
            select(PurchaseHistory).order_by(
                PurchaseHistory.po_date, PurchaseHistory.po_number
            )
        )
    )
    contracts = list(
        session.scalars(select(Contract).order_by(Contract.contract_id))
    )
    lines = list(session.scalars(select(ContractLine)))
    open_purchase_orders = list(
        session.scalars(
            select(PurchaseOrder)
            .where(
                PurchaseOrder.status.in_(
                    settings["po_consolidation"]["open_purchase_order_statuses"]
                )
            )
            .order_by(PurchaseOrder.created_at, PurchaseOrder.po_number)
        )
    )
    return {
        "items": items,
        "item_by_code": {item.item_code: item for item in items},
        "suppliers": suppliers,
        "supplier_by_id": {supplier.supplier_id: supplier for supplier in suppliers},
        "active_supplier_ids": {
            supplier.supplier_id
            for supplier in suppliers
            if supplier.status == "Active"
            and supplier.status in set(settings["supplier_eligibility"]["allowed_statuses"])
        },
        "plants": plants,
        "plant_codes": {plant.plant_code for plant in plants},
        "history": history,
        "contracts": contracts,
        "contract_lines": lines,
        "contract_lines_by_contract_item": {
            (line.contract_id, line.item_code): line for line in lines
        },
        "open_purchase_orders": open_purchase_orders,
        "settings": settings,
    }


def _available_contract_capacity(
    session: Session,
    contracts: Iterable[Contract],
) -> dict[int, Decimal]:
    capacity: dict[int, Decimal] = {}
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
        capacity[contract.id] = max(
            Decimal("0"),
            Decimal(contract.value_cap)
            - Decimal(contract.baseline_consumed_value)
            - allocated,
        )
    return capacity


def _prepare_requirement_values(
    session: Session,
    requirements: list[Requirement],
    runtime: dict[str, Any],
) -> None:
    settings = runtime["settings"]
    valuation_settings = settings["valuation"]
    contract_settings = settings["contracts"]
    capacity = _available_contract_capacity(session, runtime["contracts"])
    rule_requirements = sorted(
        requirements,
        key=lambda req: (
            req.requirement_date,
            req.first_created_at,
            req.lines[0].pr_no,
            req.lines[0].pr_line,
        ),
    )
    for requirement in rule_requirements:
        if requirement.item is None:
            price, value, source = value_requirement(
                requirement,
                valuation_settings,
                selected_contract_line=None,
                fallback_contract_line=None,
                recent_po=None,
                item_standard_price=None,
            )
            requirement.unit_price, requirement.value, requirement.price_source = price, value, source
            requirement.contract_evidence = {"applicable": False, "usable": False}
            requirement.history_evidence = {"recent_active_purchase": False}
            continue

        applicable = applicable_contracts(
            requirement.item.item_code,
            requirement.requirement_date,
            runtime["contracts"],
            runtime["contract_lines_by_contract_item"],
            runtime["active_supplier_ids"],
            contract_settings,
        )
        if contract_settings["require_pdf_extraction_without_discrepancy"]:
            applicable = [
                (contract, line)
                for contract, line in applicable
                if contract.review_status != "mismatch"
                and (
                    contract.verified
                    or (
                        contract.source_type == "pdf_manual_review"
                        and bool(contract.source_sha256)
                    )
                )
            ]

        excluded_item_types = {
            item_type.casefold()
            for item_type in contract_settings["contract_usage_excluded_item_types"]
        }
        may_use_contract = requirement.item.item_type.casefold() not in excluded_item_types
        currency_quantum = Decimal(1).scaleb(
            -int(valuation_settings["currency_scale"])
        )
        candidate_value_by_contract = {
            contract.id: (requirement.quantity * Decimal(line.rate)).quantize(
                currency_quantum
            )
            for contract, line in applicable
        }
        capacity_before = dict(capacity)
        selected = (
            select_contract_with_capacity(
                applicable, candidate_value_by_contract, capacity
            )
            if may_use_contract
            else None
        )
        selected_contract, selected_line = selected if selected else (None, None)
        has_contract_with_capacity = any(
            candidate_value_by_contract[contract.id] <= capacity.get(contract.id, Decimal("0"))
            for contract, _ in applicable
        )
        fallback_contract, fallback_line = (
            contract_for_fallback_valuation(applicable)
            if selected is None and not has_contract_with_capacity
            else None
        ) or (None, None)
        if selected_contract is not None and selected_line is not None:
            capacity[selected_contract.id] -= candidate_value_by_contract[selected_contract.id]
            requirement.contract = selected_contract
        recent_prices = recent_purchase_history(
            requirement.item.item_code,
            requirement.requirement_date,
            runtime["history"],
            int(valuation_settings["purchase_history_window_days"]),
            start_inclusive=bool(
                valuation_settings["purchase_history_window_start_inclusive"]
            ),
            end_inclusive=bool(valuation_settings["purchase_history_window_end_inclusive"]),
        )
        recent_po = recent_prices[0] if recent_prices else None
        selected_supplier_history = [
            row
            for row in recent_prices
            if row.supplier_id in runtime["active_supplier_ids"]
        ]
        if selected_supplier_history:
            latest_active_po = selected_supplier_history[0]
            requirement.history_evidence = {
                "recent_active_purchase": True,
                "most_recent_active_po_number": latest_active_po.po_number,
                "most_recent_active_supplier_id": latest_active_po.supplier_id,
                "most_recent_active_po_date": latest_active_po.po_date.isoformat(),
            }
        else:
            requirement.history_evidence = {"recent_active_purchase": False}

        price, value, source = value_requirement(
            requirement,
            valuation_settings,
            selected_contract_line=selected_line,
            fallback_contract_line=fallback_line,
            recent_po=recent_po,
            item_standard_price=Decimal(requirement.item.standard_price),
        )
        requirement.unit_price, requirement.value, requirement.price_source = price, value, source
        requirement.contract_evidence = {
            "applicable": bool(applicable),
            "usable": selected_contract is not None,
            "selected_contract_id": (
                selected_contract.contract_id if selected_contract is not None else None
            ),
            "selected_contract_database_id": (
                selected_contract.id if selected_contract is not None else None
            ),
            "rate": str(selected_line.rate) if selected_line is not None else None,
            "source_type": (
                selected_contract.source_type
                if selected_contract is not None
                else fallback_contract.source_type
                if fallback_contract is not None
                else None
            ),
            "source_reference": (
                selected_contract.source_reference
                if selected_contract is not None
                else fallback_contract.source_reference
                if fallback_contract is not None
                else None
            ),
            "source_sha256": (
                selected_contract.source_sha256
                if selected_contract is not None
                else fallback_contract.source_sha256
                if fallback_contract is not None
                else None
            ),
            "review_status": (
                selected_contract.review_status
                if selected_contract is not None
                else fallback_contract.review_status
                if fallback_contract is not None
                else None
            ),
            "verified": (
                selected_contract.verified
                if selected_contract is not None
                else fallback_contract.verified
                if fallback_contract is not None
                else None
            ),
            "fallback_contract_id": (
                fallback_contract.contract_id if fallback_contract is not None else None
            ),
            "fallback_rate": str(fallback_line.rate) if fallback_line is not None else None,
            "remaining_capacity_before": (
                str(capacity_before[selected_contract.id])
                if selected_contract is not None
                else None
            ),
            "applicable_contracts": [
                {
                    "contract_id": contract.contract_id,
                    "rate": str(line.rate),
                    "remaining_capacity": str(capacity_before[contract.id]),
                }
                for contract, line in applicable
            ],
        }


def _make_policy_decision_data(
    policy: PolicyVersion,
    settings: dict[str, Any],
    runtime: dict[str, Any],
) -> dict[str, Any]:
    return {
        "policy_version": policy.version,
        "new_item_handling": settings["new_item_handling"],
        "preferred_settings": settings["preferred_suppliers"],
        "eligibility": settings["supplier_eligibility"],
        **runtime,
    }


def _persist_run(
    session: Session,
    policy: PolicyVersion,
    run_key: str,
    requirements: list[Requirement],
    po_groups: dict[str, list[Requirement]],
    lines: list[RequestLine],
    known_plant_codes: set[str],
) -> None:
    run = DecisionRun(
        run_id=run_key,
        policy_version_id=policy.id,
        source_type="policy_engine",
        input_sha256=run_key,
        status="processing",
    )
    session.add(run)
    session.flush()

    line_models = {line.key: _existing_pr_line(session, line) for line in lines}
    requirement_models: dict[str, PurchaseRequirement] = {}
    for requirement in requirements:
        requirement.group_id = f"{run_key[:12]}-G{requirement.ordinal:04d}"
        record = PurchaseRequirement(
            group_id=requirement.group_id,
            decision_run_id=run.id,
            item_code=requirement.item.item_code if requirement.item else None,
            plant_code=(
                requirement.plant_code
                if requirement.plant_code in known_plant_codes
                else None
            ),
            requirement_date=requirement.requirement_date,
            first_created_at=requirement.first_created_at,
            need_by_date=requirement.need_by_date,
            total_quantity=requirement.quantity,
            estimated_value=requirement.value,
            is_emergency=requirement.is_emergency,
            status="not_processed" if requirement.non_processed_reason else "pending",
        )
        session.add(record)
        session.flush()
        requirement_models[requirement.group_id] = record

    po_models: dict[str, PurchaseOrder] = {}
    for po_number, po_requirements in po_groups.items():
        purchase_order = session.scalar(
            select(PurchaseOrder).where(PurchaseOrder.po_number == po_number)
        )
        if purchase_order is None:
            first = po_requirements[0]
            purchase_order = PurchaseOrder(
                po_number=po_number,
                supplier_id=first.supplier.supplier_id,
                plant_code=first.plant_code,
                route=first.route,
                contract_id=(
                    first.contract.id
                    if first.contract is not None and first.decision == "PO"
                    else None
                ),
                status="pending_approval",
            )
            session.add(purchase_order)
            session.flush()
        po_models[po_number] = purchase_order
        for requirement in po_requirements:
            model = requirement_models[requirement.group_id]
            session.add(
                PurchaseOrderLine(
                    purchase_order_id=purchase_order.id,
                    requirement_id=model.id,
                    item_code=requirement.item.item_code,
                    quantity=requirement.quantity,
                    uom=requirement.item.uom,
                    unit_price=requirement.unit_price,
                    line_value_inr=requirement.value,
                )
            )

    for requirement in requirements:
        requirement_model = requirement_models[requirement.group_id]
        purchase_order = (
            po_models.get(requirement.purchase_order_group)
            if requirement.purchase_order_group
            else None
        )
        decision = Decision(
            decision_run_id=run.id,
            requirement_id=requirement_model.id,
            purchase_order_id=purchase_order.id if purchase_order else None,
            policy_rule_id=requirement.evidence.get("policy_rule_id"),
            clause=requirement.clause or "input_validation",
            outcome=requirement.decision or "NOT_PROCESSED",
            route=requirement.route or "NOT_PROCESSED",
            supplier_id=requirement.supplier.supplier_id if requirement.supplier else None,
            contract_id=(
                requirement.contract.id
                if requirement.contract is not None and requirement.decision == "PO"
                else None
            ),
            unit_price=requirement.unit_price,
            group_value_inr=requirement.value or Decimal("0"),
            price_source=requirement.price_source,
            suggested_supplier_ids=[
                supplier.supplier_id for supplier in requirement.suggested_suppliers
            ],
            evidence=requirement.evidence,
            reason=requirement.reason,
        )
        session.add(decision)
        session.flush()
        for line in requirement.lines:
            session.add(
                DecisionLine(
                    decision_id=decision.id,
                    pr_line_id=line_models[line.key].id,
                )
            )
        if (
            requirement.contract is not None
            and requirement.decision == "PO"
            and requirement.value is not None
        ):
            session.add(
                ContractAllocation(
                    contract_id=requirement.contract.id,
                    decision_run_id=run.id,
                    requirement_id=requirement_model.id,
                    amount=requirement.value,
                    status="reserved",
                    idempotency_key=f"{run_key}:{requirement.group_id}",
                )
            )
        session.add(
            AuditEvent(
                actor_id="policy-engine",
                action="decision_generated",
                entity_type="decision",
                entity_id=f"{run_key}:{requirement.group_id}",
                reason=requirement.reason,
                after_state={
                    "policy_version": policy.version,
                    "clause": requirement.clause,
                    "route": requirement.route,
                    "decision": requirement.decision,
                    "purchase_order": requirement.purchase_order_group,
                },
            )
        )
    run.status = "completed"
    run.evaluated_at = datetime.now(timezone.utc)
    session.flush()


def process_purchase_requests(
    session: Session,
    source_lines: Iterable[RequestLine | PRLine],
    *,
    policy_version: str | int | None = None,
    as_of: date | None = None,
) -> ProcurementRunResult:
    lines = [_to_request_line(line) for line in source_lines]
    if not lines:
        raise ValueError("At least one PR line is required.")
    _ensure_unique_lines(lines)
    policy_date = as_of or min(business_date(line.created_at) for line in lines)
    policy = resolve_policy(session, policy_version, policy_date)
    run_key = _input_digest(lines, policy)
    existing_run = session.scalar(
        select(DecisionRun).where(DecisionRun.run_id == run_key)
    )
    if existing_run is not None:
        return _existing_run_result(session, existing_run, policy.version)

    settings = policy_settings(policy)
    runtime = _load_runtime_data(session, settings)
    matches = {
        line.key: match_item(line, runtime["items"], settings["item_matching"])
        for line in lines
    }
    requirements = club_lines(
        lines,
        matches,
        settings["clubbing"],
        runtime["plant_codes"],
    )
    _prepare_requirement_values(session, requirements, runtime)
    rules = ordered_rules(session, policy)
    decision_data = _make_policy_decision_data(policy, settings, runtime)
    for requirement in requirements:
        generate_decision(
            requirement,
            rules,
            settings["thresholds"],
            decision_data,
        )
    po_groups = consolidate_purchase_orders(
        requirements,
        settings["po_consolidation"],
        run_key,
        runtime["open_purchase_orders"],
    )
    _persist_run(
        session,
        policy,
        run_key,
        requirements,
        po_groups,
        lines,
        runtime["plant_codes"],
    )
    return ProcurementRunResult(run_key, policy.version, requirements, po_groups)


def override_decision(
    session: Session,
    decision_id: int,
    *,
    route: str,
    reason: str,
    actor_id: str,
) -> Decision:
    decision = session.get(Decision, decision_id)
    if decision is None:
        raise ValueError(f"Decision {decision_id} does not exist.")
    run = session.get(DecisionRun, decision.decision_run_id)
    if run is None:
        raise PolicyEvaluationError("Decision run is missing its policy-version record.")
    policy = session.get(PolicyVersion, run.policy_version_id)
    if policy is None:
        raise PolicyEvaluationError("Decision policy version is missing.")
    audit_state = explain_override(
        decision.outcome,
        decision.route,
        reason,
        actor_id,
        policy.version,
    )
    original_evidence = dict(decision.evidence)
    original_evidence["override"] = audit_state
    decision.evidence = original_evidence
    decision.overridden = True
    decision.override_reason = audit_state["override_reason"]
    decision.outcome = "PO" if route.endswith("_PO") else "SOURCING_REQUEST"
    decision.route = route
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="decision_overridden",
            entity_type="decision",
            entity_id=str(decision.id),
            reason=audit_state["override_reason"],
            before_state={
                "outcome": audit_state["original_decision"],
                "route": audit_state["original_route"],
            },
            after_state={"outcome": decision.outcome, "route": decision.route},
        )
    )
    session.flush()
    return decision


def get_preferred_supplier_list(
    session: Session,
    item_code: str,
    as_of: date,
    *,
    policy_version: str | int | None = None,
) -> list[dict[str, Any]]:
    policy = resolve_policy(session, policy_version, as_of)
    settings = policy_settings(policy)
    runtime = _load_runtime_data(session, settings)
    item = runtime["item_by_code"].get(item_code)
    if item is None:
        raise ValueError(f"Unknown item code {item_code!r}.")
    return rank_preferred_suppliers(
        item_code,
        item.category,
        as_of,
        runtime["suppliers"],
        runtime["history"],
        runtime["contracts"],
        runtime["contract_lines_by_contract_item"],
        settings["preferred_suppliers"],
    )
