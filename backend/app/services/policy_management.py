import copy
import hashlib
import json
import logging
import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import AuditEvent, PolicyRule, PolicyVersion
from app.utils.time import business_today
from app.services.policy_engine import (
    POLICY_DOCUMENT,
    SUPPORTED_OPERATORS,
    activate_due_policy_versions,
    seed_corvo_v5,
)

logger = logging.getLogger(__name__)
MAX_POLICY_PDF_BYTES = 20 * 1024 * 1024
MAX_EXTRACTED_TEXT_LENGTH = 2_000_000
MAX_POLICY_RULES = 200
ALLOWED_CONDITION_FIELDS = {
    "item.item_code",
    "item.item_type",
    "item.category",
    "plant.code",
    "requirement.quantity",
    "requirement.value",
    "requirement.is_emergency",
    "requirement.date",
    "history.recent_active_purchase",
    "contract.usable",
}
ALLOWED_DECISIONS = {"PO", "SOURCING_REQUEST"}
ALLOWED_OUTCOME_KEYS = {
    "decision",
    "route",
    "supplier_count",
    "supplier_source",
    "price_source",
    "requires_capex_approval",
}
FORBIDDEN_KEYS = {"python", "code", "script", "expression", "eval", "exec"}


class PolicyManagementError(ValueError):
    pass


def _storage_directory() -> Path:
    configured = get_settings().policy_storage_dir
    if configured.is_absolute():
        return configured
    return Path(__file__).resolve().parents[2] / configured


def _next_version(base_version: str, existing: set[str]) -> str:
    match = re.fullmatch(r"(\d+)\.(\d+)", base_version)
    if match is None:
        raise PolicyManagementError(
            "Policy version must use the supported major.minor format."
        )
    major, minor = int(match.group(1)), int(match.group(2))
    while f"{major}.{minor + 1}" in existing:
        minor += 1
    return f"{major}.{minor + 1}"


def _extract_pdf_text(content: bytes) -> str:
    if not content.startswith(b"%PDF-"):
        raise PolicyManagementError("Uploaded file is not a PDF.")
    if not content or len(content) > MAX_POLICY_PDF_BYTES:
        raise PolicyManagementError("Policy PDF must be smaller than 20 MiB.")
    try:
        import fitz

        with fitz.open(stream=content, filetype="pdf") as document:
            text = "\n".join(page.get_text("text") for page in document)
    except Exception as error:
        logger.info("Policy PDF could not be extracted", exc_info=error)
        raise PolicyManagementError("The PDF could not be parsed.") from error
    if len(text) > MAX_EXTRACTED_TEXT_LENGTH:
        raise PolicyManagementError("Extracted policy text exceeds the supported size.")
    return text


def _current_policy(session: Session, as_of: date) -> PolicyVersion:
    policy = activate_due_policies(session, as_of)
    if policy is None:
        raise PolicyManagementError(f"No approved policy is effective on {as_of}.")
    return policy


def create_policy_draft(
    session: Session,
    *,
    filename: str,
    content: bytes,
    effective_from: date,
    actor_id: str,
    policy_document: dict[str, Any] | None = None,
) -> PolicyVersion:
    if not actor_id.strip():
        raise PolicyManagementError("Uploader identity is required.")
    text = _extract_pdf_text(content)
    parent = _current_policy(session, business_today())
    base_document = copy.deepcopy(parent.policy_document)
    if base_document is None:
        base_document = json.loads(POLICY_DOCUMENT.read_text(encoding="utf-8"))
    if policy_document is not None:
        base_document = copy.deepcopy(policy_document)

    version_set = set(session.scalars(select(PolicyVersion.version)))
    version = _next_version(parent.version, version_set)
    base_document["version"] = version
    base_document["effective_from"] = effective_from.isoformat()
    digest = hashlib.sha256(content).hexdigest()

    storage = _storage_directory()
    storage.mkdir(parents=True, exist_ok=True)
    relative_file = Path("policies") / f"{digest}.pdf"
    destination = storage / f"{digest}.pdf"
    if not destination.exists():
        destination.write_bytes(content)

    draft = PolicyVersion(
        version=version,
        effective_from=effective_from,
        status="draft",
        source_reference=str(relative_file).replace("\\", "/"),
        source_sha256=digest,
        extracted_text=text,
        policy_document=base_document,
        parent_policy_version_id=parent.id,
        created_by=actor_id.strip(),
        change_summary="Draft cloned from the effective policy; review extracted PDF and edit structured policy data.",
    )
    session.add(draft)
    session.flush()
    session.add(
        AuditEvent(
            actor_id=actor_id.strip(),
            action="policy_uploaded",
            entity_type="policy_version",
            entity_id=str(draft.id),
            reason=f"Uploaded {Path(filename).name}",
            after_state={
                "version": version,
                "effective_from": effective_from.isoformat(),
                "source_sha256": digest,
                "extraction_status": "text_extracted_manual_rule_review_required",
            },
        )
    )
    return draft


def _validate_json_tree(value: Any, path: str = "policy") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise PolicyManagementError(f"{path} contains a non-string key.")
            if key.casefold() in FORBIDDEN_KEYS:
                raise PolicyManagementError(
                    f"{path}.{key} is not allowed; policy data cannot contain executable expressions."
                )
            _validate_json_tree(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _validate_json_tree(child, f"{path}[{index}]")
    elif value is not None and not isinstance(value, (str, int, float, bool)):
        raise PolicyManagementError(f"{path} contains a non-JSON value.")
    elif isinstance(value, float) and not math.isfinite(value):
        raise PolicyManagementError(f"{path} contains a non-finite number.")


def _validate_condition(condition: Any, path: str) -> None:
    if not isinstance(condition, dict):
        raise PolicyManagementError(f"{path} must be a condition object.")
    group_names = [name for name in ("all", "any", "not") if name in condition]
    if group_names:
        if len(group_names) != 1 or len(condition) != 1:
            raise PolicyManagementError(f"{path} must contain exactly one group operator.")
        name = group_names[0]
        children = condition[name]
        if not isinstance(children, list) or not children:
            raise PolicyManagementError(f"{path}.{name} must be a non-empty list.")
        if name == "not" and len(children) != 1:
            raise PolicyManagementError(f"{path}.not must contain exactly one condition.")
        for index, child in enumerate(children):
            _validate_condition(child, f"{path}.{name}[{index}]")
        return

    allowed_keys = {"field", "operator", "value", "value_from", "days"}
    if set(condition) - allowed_keys:
        raise PolicyManagementError(f"{path} contains unsupported condition keys.")
    field = condition.get("field")
    operator = condition.get("operator")
    if not isinstance(field, str) or field not in ALLOWED_CONDITION_FIELDS:
        raise PolicyManagementError(f"{path} uses unsupported field {field!r}.")
    if not isinstance(operator, str) or operator not in SUPPORTED_OPERATORS:
        raise PolicyManagementError(f"{path} uses unsupported operator {operator!r}.")
    value_kinds = {
        "item.item_code": "string",
        "item.item_type": "string",
        "item.category": "string",
        "plant.code": "string",
        "requirement.quantity": "number",
        "requirement.value": "number",
        "requirement.is_emergency": "boolean",
        "requirement.date": "date",
        "history.recent_active_purchase": "boolean",
        "contract.usable": "boolean",
    }
    kind = value_kinds[field]
    allowed_operators = {
        "string": {"eq", "ne", "in", "not_in", "contains", "not_contains", "exists"},
        "number": {"eq", "ne", "gt", "gte", "lt", "lte", "in", "not_in", "exists"},
        "boolean": {"eq", "ne", "exists"},
        "date": {"eq", "ne", "gt", "gte", "lt", "lte", "within_days", "outside_days", "exists"},
    }
    if operator not in allowed_operators[kind]:
        raise PolicyManagementError(
            f"{path} operator {operator!r} is not valid for {kind} field {field!r}."
        )
    if operator == "exists":
        if set(condition) - {"field", "operator", "value"}:
            raise PolicyManagementError(f"{path} exists condition has unsupported options.")
        if "value" not in condition:
            condition["value"] = True
        if not isinstance(condition["value"], bool):
            raise PolicyManagementError(f"{path}.value must be boolean for exists.")
    elif ("value" in condition) == ("value_from" in condition):
        raise PolicyManagementError(f"{path} must specify exactly one of value or value_from.")
    if "value_from" in condition:
        reference = condition["value_from"]
        if (
            kind != "number"
            or not isinstance(reference, str)
            or not reference.startswith("thresholds.")
        ):
            raise PolicyManagementError(f"{path}.value_from must reference policy thresholds.")
    if operator in {"within_days", "outside_days"}:
        days = condition.get("days")
        if not isinstance(days, int) or isinstance(days, bool) or days < 0:
            raise PolicyManagementError(f"{path}.days must be a non-negative integer.")
    elif "days" in condition:
        raise PolicyManagementError(f"{path}.days is only valid for date-window operators.")
    if "value" in condition and operator != "exists":
        value = condition["value"]
        if operator in {"in", "not_in"}:
            if not isinstance(value, list):
                raise PolicyManagementError(f"{path}.value must be a list for {operator}.")
            value_kind = kind
            values = value
        else:
            value_kind = kind
            values = [value]
        for expected in values:
            if value_kind == "string" and not isinstance(expected, str):
                raise PolicyManagementError(f"{path}.value must contain strings.")
            if value_kind == "boolean" and not isinstance(expected, bool):
                raise PolicyManagementError(f"{path}.value must contain booleans.")
            if value_kind == "number":
                try:
                    number = Decimal(str(expected))
                except (InvalidOperation, ValueError, TypeError) as error:
                    raise PolicyManagementError(
                        f"{path}.value must contain numeric values."
                    ) from error
                if not number.is_finite():
                    raise PolicyManagementError(f"{path}.value must contain finite numbers.")
            if value_kind == "date":
                try:
                    date.fromisoformat(str(expected))
                except ValueError as error:
                    raise PolicyManagementError(
                        f"{path}.value must contain ISO calendar dates."
                    ) from error


def validate_policy_document(
    document: dict[str, Any],
    *,
    expected_version: str,
    expected_effective_from: date,
) -> list[dict[str, Any]]:
    _validate_json_tree(document)
    required_top_level = {"document", "version", "effective_from", "source", "settings", "thresholds"}
    missing = required_top_level - set(document)
    if missing:
        raise PolicyManagementError(f"Missing policy fields: {', '.join(sorted(missing))}.")
    extra = set(document) - required_top_level
    if extra:
        raise PolicyManagementError(
            "Policy document must contain only the supported top-level fields."
        )
    for field in ("document", "version", "source"):
        if not isinstance(document[field], str) or not document[field].strip():
            raise PolicyManagementError(f"Policy field {field!r} must be a non-empty string.")
    if str(document["version"]) != expected_version:
        raise PolicyManagementError("Structured policy version must match the draft version.")
    try:
        effective_from = date.fromisoformat(str(document["effective_from"]))
    except ValueError as error:
        raise PolicyManagementError("Policy effective_from must be an ISO calendar date.") from error
    if effective_from != expected_effective_from:
        raise PolicyManagementError("Structured policy effective date must match the draft.")
    settings = document["settings"]
    if not isinstance(settings, dict):
        raise PolicyManagementError("Policy settings must be an object.")
    required_settings = {
        "item_matching",
        "clubbing",
        "valuation",
        "contracts",
        "preferred_suppliers",
        "decision_rules",
        "new_item_handling",
        "po_consolidation",
        "supplier_eligibility",
    }
    missing_settings = required_settings - set(settings)
    if missing_settings:
        raise PolicyManagementError(
            f"Missing required setting sections: {', '.join(sorted(missing_settings))}."
        )
    thresholds = document["thresholds"]
    if not isinstance(thresholds, dict) or not thresholds:
        raise PolicyManagementError("Policy thresholds must be a non-empty object.")
    for name, value in thresholds.items():
        try:
            threshold = Decimal(str(value))
            if not threshold.is_finite() or threshold < 0:
                raise ValueError
        except Exception as error:
            raise PolicyManagementError(
                f"Threshold {name!r} must be a non-negative numeric value."
            ) from error

    rules = settings["decision_rules"]
    if not isinstance(rules, list) or not rules or len(rules) > MAX_POLICY_RULES:
        raise PolicyManagementError(
            f"Policy must contain between 1 and {MAX_POLICY_RULES} decision rules."
        )
    orders: set[int] = set()
    changes: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        path = f"settings.decision_rules[{index}]"
        if not isinstance(rule, dict):
            raise PolicyManagementError(f"{path} must be an object.")
        if set(rule) != {"order", "clause", "name", "condition", "outcome"}:
            raise PolicyManagementError(
                f"{path} must contain order, clause, name, condition and outcome only."
            )
        order = rule["order"]
        if not isinstance(order, int) or isinstance(order, bool) or order <= 0 or order in orders:
            raise PolicyManagementError(f"{path}.order must be a unique positive integer.")
        orders.add(order)
        if not isinstance(rule["clause"], str) or not rule["clause"].strip():
            raise PolicyManagementError(f"{path}.clause must be a non-empty string.")
        if not isinstance(rule["name"], str) or not rule["name"].strip():
            raise PolicyManagementError(f"{path}.name must be a non-empty string.")
        _validate_condition(rule["condition"], f"{path}.condition")
        outcome = rule["outcome"]
        if not isinstance(outcome, dict) or set(outcome) - ALLOWED_OUTCOME_KEYS:
            raise PolicyManagementError(f"{path}.outcome contains unsupported fields.")
        if outcome.get("decision") not in ALLOWED_DECISIONS:
            raise PolicyManagementError(f"{path}.outcome.decision is unsupported.")
        if not isinstance(outcome.get("route"), str) or not outcome["route"].strip():
            raise PolicyManagementError(f"{path}.outcome.route must be a non-empty string.")
        count = outcome.get("supplier_count", 0)
        if not isinstance(count, int) or isinstance(count, bool) or not 0 <= count <= 100:
            raise PolicyManagementError(f"{path}.outcome.supplier_count must be from 0 to 100.")
        if outcome.get("supplier_source") not in {None, "contract", "first_preferred"}:
            raise PolicyManagementError(f"{path}.outcome.supplier_source is unsupported.")
        if outcome.get("price_source") not in {
            None,
            "contract_rate",
            "preferred_reference_price",
        }:
            raise PolicyManagementError(f"{path}.outcome.price_source is unsupported.")
        if "value_from" in json.dumps(rule["condition"]):
            for node in _condition_nodes(rule["condition"]):
                ref = node.get("value_from")
                if ref and ref.removeprefix("thresholds.") not in thresholds:
                    raise PolicyManagementError(
                        f"{path} references missing threshold {ref!r}."
                    )
        changes.append(rule)
    return changes


def _condition_nodes(condition: dict[str, Any]) -> list[dict[str, Any]]:
    if any(key in condition for key in ("all", "any", "not")):
        return [
            node
            for child in next(iter(condition.values()))
            for node in _condition_nodes(child)
        ]
    return [condition]


def validate_policy_draft(
    session: Session,
    policy: PolicyVersion,
    document: dict[str, Any],
    actor_id: str,
) -> tuple[PolicyVersion, list[dict[str, Any]]]:
    if policy.status != "draft":
        raise PolicyManagementError("Only a draft policy can be validated or edited.")
    rules = validate_policy_document(
        document,
        expected_version=policy.version,
        expected_effective_from=policy.effective_from,
    )
    base = session.get(PolicyVersion, policy.parent_policy_version_id)
    if base is None:
        raise PolicyManagementError("Draft is missing its parent policy version.")
    policy.policy_document = copy.deepcopy(document)
    session.execute(delete(PolicyRule).where(PolicyRule.policy_version_id == policy.id))
    session.flush()
    for rule in rules:
        session.add(
            PolicyRule(
                policy_version_id=policy.id,
                order=rule["order"],
                clause=rule["clause"],
                name=rule["name"],
                condition=rule["condition"],
                outcome=rule["outcome"],
            )
        )
    changes = _document_diff(
        base.policy_document or json.loads(POLICY_DOCUMENT.read_text(encoding="utf-8")),
        document,
    )
    policy.change_summary = _human_summary(changes, base.version, policy.version)
    policy.status = "validated"
    session.add(
        AuditEvent(
            actor_id=actor_id,
            action="policy_validated",
            entity_type="policy_version",
            entity_id=str(policy.id),
            reason=policy.change_summary,
            after_state={
                "version": policy.version,
                "rule_count": len(rules),
                "change_count": len(changes),
            },
        )
    )
    session.flush()
    return policy, changes


def _document_diff(before: Any, after: Any, path: str = "") -> list[dict[str, Any]]:
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[dict[str, Any]] = []
        for key in sorted(before.keys() | after.keys()):
            child_path = f"{path}.{key}" if path else key
            if key not in before:
                changes.append({"path": child_path, "before": None, "after": after[key]})
            elif key not in after:
                changes.append({"path": child_path, "before": before[key], "after": None})
            else:
                changes.extend(_document_diff(before[key], after[key], child_path))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        changes = []
        max_length = max(len(before), len(after))
        for index in range(max_length):
            child_path = f"{path}[{index}]"
            if index >= len(before):
                changes.append({"path": child_path, "before": None, "after": after[index]})
            elif index >= len(after):
                changes.append({"path": child_path, "before": before[index], "after": None})
            else:
                changes.extend(_document_diff(before[index], after[index], child_path))
        return changes
    if before != after:
        return [{"path": path, "before": before, "after": after}]
    return []


def _human_summary(changes: list[dict[str, Any]], base_version: str, version: str) -> str:
    if not changes:
        return f"Policy {version} has no functional changes from {base_version}."
    summaries = []
    threshold_labels = {
        "spot_maximum_value": "Spot purchase",
        "emergency_maximum_value": "Emergency purchase",
        "high_value_minimum_value": "High-value purchase",
        "capex_minimum_value": "Capex",
    }
    for change in changes:
        path = change["path"]
        if path.startswith("thresholds."):
            threshold_name = path.removeprefix("thresholds.")
            threshold_label = threshold_labels.get(
                threshold_name, threshold_name.replace("_", " ").capitalize()
            )
            label = f"{threshold_label} threshold"
        elif ".creation_window_days" in path or ".need_by_window_days" in path or ".lookback_days" in path:
            label = f"{path.rsplit('.', 1)[-1].replace('_', ' ')} window"
        elif "supplier_eligibility" in path:
            label = "Supplier eligibility"
        elif ".decision_rules[" in path:
            label = "Decision rule"
        else:
            label = path
        summaries.append(f"{label}: {change['before']!r} → {change['after']!r}")
    return f"Policy {version} compared with {base_version}: " + "; ".join(summaries[:30])


def policy_diff(session: Session, policy: PolicyVersion) -> dict[str, Any]:
    base = session.get(PolicyVersion, policy.parent_policy_version_id)
    if base is None:
        raise PolicyManagementError("Policy is missing its base version for comparison.")
    base_document = base.policy_document
    candidate_document = policy.policy_document
    if base_document is None or candidate_document is None:
        raise PolicyManagementError("Policy versions are missing structured policy data.")
    changes = _document_diff(base_document, candidate_document)
    summary = policy.change_summary or _human_summary(changes, base.version, policy.version)
    return {
        "policy_id": policy.id,
        "version": policy.version,
        "base_version": base.version,
        "status": policy.status,
        "effective_from": policy.effective_from,
        "summary": summary,
        "changes": changes,
    }


def approve_policy(
    session: Session,
    policy: PolicyVersion,
    *,
    actor_id: str,
    reason: str,
    today: date | None = None,
) -> PolicyVersion:
    if policy.status != "validated":
        raise PolicyManagementError("Only a validated policy can be approved.")
    if not actor_id.strip() or not reason.strip():
        raise PolicyManagementError("Approver identity and approval reason are required.")
    effective_date = today or business_today()
    if policy.effective_from <= effective_date:
        _activate_policy(session, policy, effective_date)
    else:
        policy.status = "approved"
    policy.approved_by = actor_id.strip()
    policy.approved_at = datetime.now(timezone.utc)
    session.add(
        AuditEvent(
            actor_id=actor_id.strip(),
            action="policy_approved",
            entity_type="policy_version",
            entity_id=str(policy.id),
            reason=reason.strip(),
            after_state={
                "version": policy.version,
                "effective_from": policy.effective_from.isoformat(),
                "status": policy.status,
            },
        )
    )
    session.flush()
    return policy


def _activate_policy(session: Session, selected: PolicyVersion, as_of: date) -> None:
    for current in session.scalars(
        select(PolicyVersion).where(PolicyVersion.status == "active")
    ):
        if current.id != selected.id and current.effective_from <= as_of:
            current.status = "superseded"
    selected.status = "active"


def activate_due_policies(session: Session, as_of: date) -> PolicyVersion | None:
    seed_corvo_v5(session)
    return activate_due_policy_versions(session, as_of)


def reject_policy(
    session: Session,
    policy: PolicyVersion,
    *,
    actor_id: str,
    reason: str,
) -> PolicyVersion:
    if policy.status not in {"draft", "validated"}:
        raise PolicyManagementError("Only a draft or validated policy can be rejected.")
    if not actor_id.strip() or not reason.strip():
        raise PolicyManagementError("Rejector identity and rejection reason are required.")
    policy.status = "rejected"
    session.add(
        AuditEvent(
            actor_id=actor_id.strip(),
            action="policy_rejected",
            entity_type="policy_version",
            entity_id=str(policy.id),
            reason=reason.strip(),
            after_state={"version": policy.version, "status": policy.status},
        )
    )
    session.flush()
    return policy
