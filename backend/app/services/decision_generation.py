from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.models import PolicyRule
from app.services.policy_engine import first_matching_rule
from app.services.procurement_types import Requirement
from app.services.supplier_ranking import (
    rank_preferred_suppliers,
    suggest_suppliers,
    supplier_is_eligible,
)


def _ranked_suppliers(requirement: Requirement, data: Mapping[str, Any]) -> list[dict[str, Any]]:
    if requirement.item is None:
        return []
    return rank_preferred_suppliers(
        requirement.item.item_code,
        requirement.item.category,
        requirement.requirement_date,
        data["suppliers"],
        data["history"],
        data["contracts"],
        data["contract_lines_by_contract_item"],
        data["preferred_settings"],
    )


def generate_decision(
    requirement: Requirement,
    rules: list[PolicyRule],
    thresholds: Mapping[str, Any],
    data: Mapping[str, Any],
) -> Requirement:
    if requirement.non_processed_reason:
        requirement.decision = "NOT_PROCESSED"
        requirement.route = "NOT_PROCESSED"
        requirement.clause = "input_validation"
        requirement.reason = requirement.non_processed_reason
        requirement.evidence = {"policy_version": data["policy_version"]}
        return requirement

    if requirement.value is None or requirement.unit_price is None:
        requirement.decision = "NOT_PROCESSED"
        requirement.route = "NOT_PROCESSED"
        requirement.clause = "3.1"
        requirement.reason = (
            "Requirement cannot be valued from the policy price sources; "
            "a requester estimate may be required."
        )
        requirement.evidence = {
            "price_source": requirement.price_source,
            "policy_version": data["policy_version"],
        }
        return requirement

    if requirement.item is None:
        handling = data["new_item_handling"]
        requirement.decision = handling["decision"]
        requirement.route = handling["route"]
        requirement.clause = handling["clause"]
        requirement.supplier = None
        requirement.suggested_suppliers = []
        requirement.reason = (
            f"Policy {data['policy_version']} clause {requirement.clause}: item "
            f"{requirement.lines[0].description!r} did not resolve to the item master; "
            f"valued from requester estimate."
        )
        requirement.evidence = {
            "item_match_method": requirement.match.method if requirement.match else "unmatched",
            "candidate_item_codes": (
                [code for code, _ in requirement.match.candidates]
                if requirement.match
                else []
            ),
            "price_source": requirement.price_source,
            "price": str(requirement.unit_price),
            "value": str(requirement.value),
            "policy_version": data["policy_version"],
        }
        return requirement

    context = {
        "item": {
            "item_code": requirement.item.item_code,
            "item_type": requirement.item.item_type,
            "category": requirement.item.category,
        },
        "plant": {"code": requirement.plant_code},
        "requirement": {
            "quantity": requirement.quantity,
            "value": requirement.value,
            "is_emergency": requirement.is_emergency,
            "date": requirement.requirement_date,
        },
        "history": requirement.history_evidence,
        "contract": requirement.contract_evidence,
        "thresholds": thresholds,
    }
    threshold_value = requirement.value
    valuation_unit_price = requirement.unit_price
    valuation_price_source = requirement.price_source
    rule = first_matching_rule(rules, context)
    outcome = rule.outcome
    requirement.decision = outcome["decision"]
    requirement.route = outcome["route"]
    requirement.clause = rule.clause

    rankings = _ranked_suppliers(requirement, data)
    ranked_evidence = [
        {
            "supplier_id": entry["supplier"].supplier_id,
            "score": str(entry["score"]),
            "purchase_count": entry["purchase_count"],
            "most_recent_po_date": (
                entry["most_recent_po_date"].isoformat()
                if entry["most_recent_po_date"]
                else None
            ),
            "recency_score": str(entry["recency_score"]),
            "contract_history": entry["contract_history"],
            "on_time_score": str(entry["on_time_score"]),
            "quality_score": str(entry["quality_score"]),
            "reference_price": str(entry["reference_price"]),
            "reference_source": entry["reference_source"],
        }
        for entry in rankings
    ]

    if outcome.get("supplier_source") == "contract":
        if requirement.contract is None:
            raise RuntimeError(f"Rule {rule.clause} requires a contract supplier.")
        requirement.supplier = data["supplier_by_id"][requirement.contract.supplier_id]
        requirement.unit_price = Decimal(requirement.contract_evidence["rate"])
        requirement.price_source = "contract_rate"
    elif outcome.get("supplier_source") == "first_preferred":
        if not rankings:
            raise RuntimeError(
                f"Rule {rule.clause} matched without an eligible preferred supplier."
            )
        else:
            preferred = rankings[0]
            requirement.supplier = preferred["supplier"]
            if outcome.get("price_source") == "preferred_reference_price":
                requirement.unit_price = Decimal(preferred["reference_price"])
                currency_scale = int(data["settings"]["valuation"]["currency_scale"])
                quantum = Decimal(1).scaleb(-currency_scale)
                requirement.value = (
                    requirement.quantity * requirement.unit_price
                ).quantize(quantum)
                requirement.price_source = preferred["reference_source"]
    elif outcome.get("supplier_count", 0):
        requirement.suggested_suppliers = suggest_suppliers(
            rankings,
            data["suppliers"],
            requirement.item.category,
            int(outcome["supplier_count"]),
            set(data["eligibility"]["allowed_statuses"]),
            data["preferred_settings"],
        )

    allowed_statuses = set(data["eligibility"]["allowed_statuses"])
    if requirement.supplier is not None and not supplier_is_eligible(
        requirement.supplier, allowed_statuses
    ):
        raise RuntimeError(
            f"Supplier eligibility invariant violated for {requirement.supplier.supplier_id}."
        )
    if any(
        not supplier_is_eligible(supplier, allowed_statuses)
        for supplier in requirement.suggested_suppliers
    ):
        raise RuntimeError("Supplier eligibility invariant violated in sourcing suggestions.")

    requirement.evidence = {
        "policy_version": data["policy_version"],
        "policy_rule_id": rule.id,
        "rule_order": rule.order,
        "rule_name": rule.name,
        "item_match_method": requirement.match.method if requirement.match else "provided_code",
        "item_match_score": str(requirement.match.score) if requirement.match else "1",
        "item_candidates": (
            [{"item_code": code, "score": str(score)} for code, score in requirement.match.candidates]
            if requirement.match
            else []
        ),
        "price_source": requirement.price_source,
        "unit_price": str(requirement.unit_price),
        "group_value_inr": str(requirement.value),
        "threshold_value_inr": str(threshold_value),
        "valuation_unit_price_before_supplier_selection": str(valuation_unit_price),
        "valuation_price_source_before_supplier_selection": valuation_price_source,
        "history": requirement.history_evidence,
        "contract": requirement.contract_evidence,
        "preferred_supplier_ranking": ranked_evidence,
        "supplier_id": requirement.supplier.supplier_id if requirement.supplier else None,
        "suggested_supplier_ids": [
            supplier.supplier_id for supplier in requirement.suggested_suppliers
        ],
    }
    supplier_text = (
        f" Supplier {requirement.supplier.supplier_id} selected at INR "
        f"{requirement.unit_price}."
        if requirement.supplier
        else ""
    )
    contract_text = (
        f" Contract {requirement.contract.contract_id}."
        + (
            f" PDF source {requirement.contract.source_reference}; verification "
            f"status {requirement.contract.review_status}."
            if requirement.contract.source_type == "pdf_manual_review"
            else ""
        )
        if requirement.contract is not None
        else ""
    )
    history_text = (
        f" Recent active-supplier purchase found: "
        f"{requirement.history_evidence.get('most_recent_active_po_number')}."
        if requirement.history_evidence.get("recent_active_purchase")
        else ""
    )
    requirement.reason = (
        f"Policy {data['policy_version']} clause {rule.clause} "
        f"({rule.name}); requirement value INR {requirement.value}."
        f"{contract_text}{history_text}{supplier_text}"
    )
    return requirement


def explain_override(
    original_decision: str,
    original_route: str,
    reason: str,
    actor_id: str,
    policy_version: str,
) -> dict[str, Any]:
    cleaned_reason = reason.strip()
    if not cleaned_reason:
        raise ValueError("A written reason is required to override a decision.")
    if not actor_id.strip():
        raise ValueError("An actor ID is required for an audited override.")
    return {
        "original_decision": original_decision,
        "original_route": original_route,
        "override_reason": cleaned_reason,
        "overridden_by": actor_id,
        "policy_version": policy_version,
    }
