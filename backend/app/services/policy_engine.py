import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PolicyRule, PolicyVersion

POLICY_DOCUMENT = Path(__file__).resolve().parents[1] / "policies" / "corvo_v5.json"
POLICY_DOCUMENT_DIRECTORY = POLICY_DOCUMENT.parent
SUPPORTED_OPERATORS = {
    "eq",
    "ne",
    "gt",
    "gte",
    "lt",
    "lte",
    "in",
    "not_in",
    "contains",
    "not_contains",
    "within_days",
    "outside_days",
    "exists",
}


class PolicyEvaluationError(ValueError):
    pass


def seed_corvo_v5(session: Session) -> PolicyVersion:
    document = json.loads(POLICY_DOCUMENT.read_text(encoding="utf-8"))
    existing = session.scalar(
        select(PolicyVersion).where(PolicyVersion.version == document["version"])
    )
    if existing is not None:
        if existing.policy_document is None:
            existing.policy_document = document
        return existing

    policy = PolicyVersion(
        version=document["version"],
        effective_from=date.fromisoformat(document["effective_from"]),
        status="active",
        source_reference=document["source"],
        policy_document=document,
        change_summary="Corvo Engineering procurement policy version 5.0",
    )
    session.add(policy)
    session.flush()
    for rule in document["settings"]["decision_rules"]:
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
    session.flush()
    return policy


def resolve_policy(
    session: Session,
    explicit_version: str | int | None,
    as_of: date,
) -> PolicyVersion:
    seed_corvo_v5(session)
    if explicit_version is not None:
        statement = (
            select(PolicyVersion)
            .where(PolicyVersion.version == str(explicit_version))
            .where(PolicyVersion.effective_from <= as_of)
        )
        policy = session.scalar(statement)
        if policy is None or policy.status not in {"active", "approved", "superseded"}:
            raise PolicyEvaluationError(
                f"Policy version {explicit_version!r} is unavailable or not effective on {as_of}."
            )
        return policy

    activate_due_policy_versions(session, as_of)
    policy = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.status.in_(("active", "approved", "superseded")))
        .where(PolicyVersion.effective_from <= as_of)
        .order_by(PolicyVersion.effective_from.desc(), PolicyVersion.id.desc())
        .limit(1)
    )
    if policy is None:
        raise PolicyEvaluationError(f"No active policy is effective on {as_of}.")
    return policy


def activate_due_policy_versions(session: Session, as_of: date) -> PolicyVersion | None:
    selected = session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.status == "approved")
        .where(PolicyVersion.effective_from <= as_of)
        .order_by(PolicyVersion.effective_from.desc(), PolicyVersion.id.desc())
        .limit(1)
    )
    if selected is not None:
        for policy in session.scalars(
            select(PolicyVersion)
            .where(PolicyVersion.status.in_(("active", "approved")))
            .where(PolicyVersion.effective_from <= as_of)
        ):
            policy.status = "active" if policy.id == selected.id else "superseded"
        session.flush()
    return session.scalar(
        select(PolicyVersion)
        .where(PolicyVersion.status == "active")
        .where(PolicyVersion.effective_from <= as_of)
        .order_by(PolicyVersion.effective_from.desc(), PolicyVersion.id.desc())
        .limit(1)
    )


def policy_settings(policy: PolicyVersion) -> dict[str, Any]:
    if policy.policy_document is not None:
        document = policy.policy_document
        return {**document["settings"], "thresholds": document["thresholds"]}
    for path in sorted(POLICY_DOCUMENT_DIRECTORY.glob("*.json")):
        document = json.loads(path.read_text(encoding="utf-8"))
        if str(document.get("version")) == policy.version:
            return {**document["settings"], "thresholds": document["thresholds"]}
    raise PolicyEvaluationError(
        f"Policy {policy.version!r} has no versioned runtime settings document."
    )


def _resolve_path(context: Mapping[str, Any], path: str) -> Any:
    value: Any = context
    for part in path.split("."):
        if isinstance(value, Mapping):
            if part not in value:
                raise PolicyEvaluationError(f"Unknown policy condition field: {path!r}")
            value = value[part]
        else:
            raise PolicyEvaluationError(f"Cannot resolve policy condition field: {path!r}")
    return value


def _resolve_expected(expression: Mapping[str, Any], context: Mapping[str, Any]) -> Any:
    if "value_from" in expression:
        return _resolve_path(context, str(expression["value_from"]))
    if "value" not in expression:
        raise PolicyEvaluationError("A policy comparison must specify value or value_from.")
    return expression["value"]


def _as_decimal(value: Any) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise PolicyEvaluationError(f"Cannot compare non-numeric value {value!r}.") from error


def _as_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value))


def evaluate_condition(expression: Mapping[str, Any], context: Mapping[str, Any]) -> bool:
    groups = [key for key in ("all", "any", "not") if key in expression]
    if groups:
        if len(groups) != 1 or len(expression) != 1:
            raise PolicyEvaluationError("Condition group must contain exactly one group operator.")
        group = groups[0]
        children = expression[group]
        if not isinstance(children, list):
            raise PolicyEvaluationError(f"Condition group {group!r} must contain a list.")
        results = [evaluate_condition(child, context) for child in children]
        if group == "all":
            return all(results)
        if group == "any":
            return any(results)
        if len(results) != 1:
            raise PolicyEvaluationError("'not' condition group must contain exactly one expression.")
        return not results[0]

    if set(expression) - {"field", "operator", "value", "value_from", "days"}:
        raise PolicyEvaluationError("Condition contains unsupported keys.")
    field = expression.get("field")
    operator = expression.get("operator")
    if not isinstance(field, str) or operator not in SUPPORTED_OPERATORS:
        raise PolicyEvaluationError(f"Invalid condition field/operator: {field!r}/{operator!r}.")
    actual = _resolve_path(context, field)
    if operator == "exists":
        return (actual is not None) is bool(expression.get("value", True))

    expected = _resolve_expected(expression, context)
    if operator in {"eq", "ne", "gt", "gte", "lt", "lte"}:
        if isinstance(actual, (int, float, Decimal)) and not isinstance(actual, bool):
            actual_value: Any = _as_decimal(actual)
            expected_value: Any = _as_decimal(expected)
        else:
            actual_value = actual
            expected_value = expected
        comparison = {
            "eq": actual_value == expected_value,
            "ne": actual_value != expected_value,
            "gt": actual_value > expected_value,
            "gte": actual_value >= expected_value,
            "lt": actual_value < expected_value,
            "lte": actual_value <= expected_value,
        }
        return comparison[operator]
    if operator == "in":
        return actual in expected
    if operator == "not_in":
        return actual not in expected
    if operator == "contains":
        return expected in actual
    if operator == "not_contains":
        return expected not in actual
    if operator in {"within_days", "outside_days"}:
        if "days" not in expression:
            raise PolicyEvaluationError(f"Operator {operator!r} requires 'days'.")
        difference = abs((_as_date(actual) - _as_date(expected)).days)
        within = difference <= int(expression["days"])
        return within if operator == "within_days" else not within
    raise PolicyEvaluationError(f"Unsupported operator {operator!r}.")


def ordered_rules(session: Session, policy: PolicyVersion) -> list[PolicyRule]:
    return list(
        session.scalars(
            select(PolicyRule)
            .where(PolicyRule.policy_version_id == policy.id)
            .order_by(PolicyRule.order, PolicyRule.id)
        )
    )


def first_matching_rule(
    rules: list[PolicyRule], context: Mapping[str, Any]
) -> PolicyRule:
    for rule in rules:
        if evaluate_condition(rule.condition, context):
            return rule
    raise PolicyEvaluationError(
        "No decision rule matched; policy is incomplete for this procurement context."
    )
