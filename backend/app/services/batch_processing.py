import csv
import hashlib
import io
import uuid
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import BatchRun, PRLine
from app.services.policy_engine import policy_settings, resolve_policy
from app.services.procurement_engine import (
    _load_runtime_data,
    process_purchase_requests,
)
from app.services.procurement_types import ProcurementRunResult, RequestLine
from app.services.supplier_ranking import rank_preferred_suppliers
from app.utils.time import business_date, normalize_datetime

OUTPUT_COLUMNS = [
    "pr_no",
    "pr_line",
    "resolved_item_code",
    "group_id",
    "po_id",
    "decision",
    "route",
    "supplier_id",
    "unit_price",
    "group_value_inr",
    "suggested_supplier_ids",
    "policy_version",
    "reason",
]
INPUT_COLUMNS = {
    "pr_no",
    "pr_line",
    "created_at",
    "requester",
    "department",
    "plant",
    "item_code",
    "description",
    "quantity",
    "uom",
    "estimated_unit_price",
    "need_by_date",
    "urgency",
}
MAX_BATCH_BYTES = 25 * 1024 * 1024


class BatchInputError(ValueError):
    pass


def _not_processed(
    raw: dict[str, Any],
    reason: str,
    *,
    policy_version: str = "",
) -> dict[str, Any]:
    return {
        "pr_no": str(raw.get("pr_no") or "").strip(),
        "pr_line": str(raw.get("pr_line") or "").strip(),
        "resolved_item_code": str(raw.get("item_code") or "").strip() or "NEW",
        "group_id": "",
        "po_id": "",
        "decision": "NOT_PROCESSED",
        "route": "NOT_PROCESSED",
        "supplier_id": "",
        "unit_price": "",
        "group_value_inr": "",
        "suggested_supplier_ids": "",
        "policy_version": policy_version,
        "reason": reason,
    }


def _decimal(
    value: str,
    field: str,
    *,
    optional: bool = False,
    precision: int,
    scale: int,
) -> Decimal | None:
    if not value.strip():
        if optional:
            return None
        raise ValueError(f"{field} is required.")
    try:
        result = Decimal(value.strip())
    except InvalidOperation as error:
        raise ValueError(f"{field} must be a decimal number.") from error
    if not result.is_finite():
        raise ValueError(f"{field} must be finite.")
    normalized = result.normalize().as_tuple()
    actual_precision = len(normalized.digits) + max(normalized.exponent, 0)
    actual_scale = max(-normalized.exponent, 0)
    if actual_precision > precision or actual_scale > scale:
        raise ValueError(
            f"{field} exceeds the supported precision ({precision},{scale})."
        )
    return result


def _parse_row(raw: dict[str, Any]) -> RequestLine:
    if None in raw:
        raise ValueError("Row has more fields than the CSV header.")
    values = {key: (value or "").strip() for key, value in raw.items()}
    for key in ("pr_no", "pr_line", "created_at", "requester", "department", "plant",
                "description", "quantity", "uom", "need_by_date", "urgency"):
        if not values.get(key):
            raise ValueError(f"{key} is required.")
    if not values["item_code"] and not values["description"]:
        raise ValueError("Either item_code or description is required.")
    try:
        line_number = int(values["pr_line"])
    except ValueError as error:
        raise ValueError("pr_line must be a positive integer.") from error
    if line_number <= 0:
        raise ValueError("pr_line must be a positive integer.")
    try:
        created_at = datetime.fromisoformat(values["created_at"])
    except ValueError as error:
        raise ValueError("created_at must be an ISO datetime.") from error
    created_at = normalize_datetime(created_at)
    try:
        need_by_date = date.fromisoformat(values["need_by_date"])
    except ValueError as error:
        raise ValueError("need_by_date must be an ISO date.") from error
    quantity = _decimal(values["quantity"], "quantity", precision=16, scale=3)
    estimated_price = _decimal(
        values["estimated_unit_price"],
        "estimated_unit_price",
        optional=True,
        precision=14,
        scale=2,
    )
    if quantity is None or quantity <= 0:
        raise ValueError("quantity must be greater than zero.")
    if estimated_price is not None and estimated_price < 0:
        raise ValueError("estimated_unit_price cannot be negative.")
    if (
        len(values["pr_no"]) > 64
        or len(values["plant"]) > 32
        or len(values["requester"]) > 255
        or len(values["department"]) > 100
        or len(values["uom"]) > 24
        or len(values["item_code"]) > 32
    ):
        raise ValueError("A field exceeds its supported maximum length.")
    if len(values["urgency"]) > 24:
        raise ValueError("urgency exceeds the supported length.")
    return RequestLine(
        pr_no=values["pr_no"],
        pr_line=line_number,
        created_at=created_at,
        requester=values["requester"],
        department=values["department"],
        plant_code=values["plant"],
        supplied_item_code=values["item_code"] or None,
        description=values["description"],
        quantity=quantity,
        uom=values["uom"],
        estimated_unit_price=estimated_price,
        need_by_date=need_by_date,
        urgency=values["urgency"],
    )


def _decode_csv(content: bytes) -> list[tuple[int, dict[str, Any]]]:
    if not content:
        raise BatchInputError("CSV upload is empty.")
    if len(content) > MAX_BATCH_BYTES:
        raise BatchInputError("CSV upload exceeds the 25 MiB limit.")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise BatchInputError("CSV must be encoded as UTF-8.") from error
    try:
        reader = csv.DictReader(io.StringIO(text, newline=""), strict=True)
        if reader.fieldnames is None:
            raise BatchInputError("CSV must include a header row.")
        if any(not isinstance(header, str) or not header.strip() for header in reader.fieldnames):
            raise BatchInputError("CSV header contains an empty column name.")
        headers = [header.strip() for header in reader.fieldnames]
        if len(headers) != len(set(headers)):
            raise BatchInputError("CSV contains duplicate column names.")
        missing = INPUT_COLUMNS - set(headers)
        if missing:
            raise BatchInputError(
                f"CSV is missing required columns: {', '.join(sorted(missing))}."
            )
        reader.fieldnames = headers
        rows = [
            (line_number, row)
            for line_number, row in enumerate(reader, start=2)
            if row and any(value for value in row.values() if value is not None)
        ]
    except csv.Error as error:
        raise BatchInputError(f"CSV syntax error: {error}.") from error
    if not rows:
        raise BatchInputError("CSV contains no PR lines.")
    return rows


def _persisted_conflict(session: Session, line: RequestLine) -> bool:
    existing = session.scalar(
        select(PRLine).where(PRLine.pr_no == line.pr_no, PRLine.pr_line == line.pr_line)
    )
    if existing is None:
        return False
    created = normalize_datetime(existing.created_at_source, naive_is_business=False)
    line_created = normalize_datetime(line.created_at)
    return not (
        created == line_created
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


def _requirement_rows(run: ProcurementRunResult) -> dict[tuple[str, int], dict[str, Any]]:
    result: dict[tuple[str, int], dict[str, Any]] = {}
    for requirement in run.requirements:
        suggestions = ";".join(
            supplier.supplier_id for supplier in requirement.suggested_suppliers
        )
        supplier_id = (
            requirement.supplier.supplier_id
            if requirement.supplier is not None
            else suggestions
        )
        output = {
            "resolved_item_code": (
                requirement.item.item_code if requirement.item is not None else "NEW"
            ),
            "group_id": requirement.group_id,
            "po_id": requirement.purchase_order_group or "",
            "decision": requirement.decision or "NOT_PROCESSED",
            "route": requirement.route or "NOT_PROCESSED",
            "supplier_id": supplier_id,
            "unit_price": (
                f"{requirement.unit_price:.2f}"
                if requirement.decision == "PO" and requirement.unit_price is not None
                else ""
            ),
            "group_value_inr": (
                f"{requirement.value:.2f}" if requirement.value is not None else ""
            ),
            "suggested_supplier_ids": suggestions,
            "policy_version": run.policy_version,
            "reason": requirement.reason,
        }
        for line in requirement.lines:
            result[line.key] = {
                "pr_no": line.pr_no,
                "pr_line": line.pr_line,
                **output,
            }
    return result


def process_batch_csv(session: Session, content: bytes) -> BatchRun:
    source_rows = _decode_csv(content)
    output_by_index: dict[int, dict[str, Any]] = {}
    valid_lines: list[tuple[int, RequestLine]] = []
    seen_keys: set[tuple[str, int]] = set()
    errors: list[dict[str, Any]] = []

    for row_number, raw in source_rows:
        raw_pr_no = str(raw.get("pr_no") or "").strip()
        try:
            raw_pr_line = int(str(raw.get("pr_line") or "").strip())
            duplicate_key = (
                (raw_pr_no, raw_pr_line)
                if raw_pr_no and raw_pr_line > 0
                else None
            )
        except ValueError:
            duplicate_key = None
        if duplicate_key is not None and duplicate_key in seen_keys:
            reason = (
                f"Duplicate PR line {duplicate_key[0]}/{duplicate_key[1]} "
                "in uploaded CSV."
            )
            output_by_index[row_number] = _not_processed(raw, reason)
            errors.append(
                {
                    "row": row_number,
                    "pr_no": duplicate_key[0],
                    "pr_line": duplicate_key[1],
                    "reason": reason,
                    "type": "duplicate",
                }
            )
            continue
        if duplicate_key is not None:
            seen_keys.add(duplicate_key)
        try:
            line = _parse_row(raw)
        except ValueError as error:
            output_by_index[row_number] = _not_processed(raw, str(error))
            errors.append(
                {
                    "row": row_number,
                    "pr_no": str(raw.get("pr_no") or "").strip(),
                    "pr_line": str(raw.get("pr_line") or "").strip(),
                    "reason": str(error),
                    "type": "input",
                }
            )
            continue
        if _persisted_conflict(session, line):
            reason = f"PR line {line.pr_no}/{line.pr_line} already exists with different source data."
            output_by_index[row_number] = _not_processed(raw, reason)
            errors.append(
                {
                    "row": row_number,
                    "pr_no": line.pr_no,
                    "pr_line": line.pr_line,
                    "reason": reason,
                    "type": "duplicate",
                }
            )
            continue
        valid_lines.append((row_number, line))

    grouped: dict[str, list[tuple[int, RequestLine]]] = defaultdict(list)
    for row_number, line in valid_lines:
        requirement_date = business_date(line.created_at)
        try:
            policy = resolve_policy(session, None, requirement_date)
            grouped[policy.version].append((row_number, line))
        except ValueError as error:
            reason = f"No effective procurement policy for {requirement_date}: {error}"
            output_by_index[row_number] = _not_processed(
                {
                    "pr_no": line.pr_no,
                    "pr_line": line.pr_line,
                    "item_code": line.supplied_item_code,
                },
                reason,
            )
            errors.append({"row": row_number, "reason": reason, "type": "policy"})

    for version, entries in sorted(
        grouped.items(),
        key=lambda pair: min(line.created_at for _, line in pair[1]),
    ):
        entries.sort(key=lambda pair: (pair[1].created_at, pair[1].pr_no, pair[1].pr_line))
        try:
            run = process_purchase_requests(
                session,
                [line for _, line in entries],
                policy_version=version,
                as_of=min(business_date(line.created_at) for _, line in entries),
            )
            session.flush()
        except ValueError as error:
            reason = f"Batch engine rejected policy {version}: {error}"
            for row_number, line in entries:
                output_by_index[row_number] = _not_processed(
                    {
                        "pr_no": line.pr_no,
                        "pr_line": line.pr_line,
                        "item_code": line.supplied_item_code,
                    },
                    reason,
                    policy_version=version,
                )
                errors.append({"row": row_number, "reason": reason, "type": "engine"})
            continue
        result_map = _requirement_rows(run)
        for row_number, line in entries:
            output_by_index[row_number] = result_map[line.key]
            row = result_map[line.key]
            if row["decision"] == "NOT_PROCESSED":
                errors.append(
                    {
                        "row": row_number,
                        "pr_no": line.pr_no,
                        "pr_line": line.pr_line,
                        "reason": row["reason"],
                        "type": "decision",
                    }
                )

    result_rows = [
        output_by_index[row_number]
        for row_number, _ in source_rows
    ]
    not_processed = sum(row["decision"] == "NOT_PROCESSED" for row in result_rows)
    stats = {
        "total_rows": len(result_rows),
        "processed": len(result_rows) - not_processed,
        "not_processed": not_processed,
        "duplicates": sum(error.get("type") == "duplicate" for error in errors),
        "errors": len(errors),
        "routes": {
            route: sum(row["route"] == route for row in result_rows)
            for route in sorted({row["route"] for row in result_rows})
        },
        "policy_versions": sorted({row["policy_version"] for row in result_rows if row["policy_version"]}),
    }
    batch = BatchRun(
        id=str(uuid.uuid4()),
        input_sha256=hashlib.sha256(content).hexdigest(),
        result_rows=result_rows,
        statistics={**stats, "error_details": errors},
        policy_versions=stats["policy_versions"],
    )
    session.add(batch)
    session.flush()
    return batch


def preferred_supplier_rows(
    session: Session,
    as_of: date,
) -> tuple[str, list[dict[str, str]]]:
    policy = resolve_policy(session, None, as_of)
    settings = policy_settings(policy)
    runtime = _load_runtime_data(session, settings)
    result: list[dict[str, str]] = []
    for item in runtime["items"]:
        ranked = rank_preferred_suppliers(
            item.item_code,
            item.category,
            as_of,
            runtime["suppliers"],
            runtime["history"],
            runtime["contracts"],
            runtime["contract_lines_by_contract_item"],
            settings["preferred_suppliers"],
        )
        for rank, entry in enumerate(ranked, start=1):
            result.append(
                {
                    "item_code": item.item_code,
                    "rank": str(rank),
                    "supplier_id": entry["supplier"].supplier_id,
                    "reference_price": str(Decimal(entry["reference_price"])),
                    "top_three": "true" if rank <= 3 else "false",
                }
            )
    return policy.version, result
