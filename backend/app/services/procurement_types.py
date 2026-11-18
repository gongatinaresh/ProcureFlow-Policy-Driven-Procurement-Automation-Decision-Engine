from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class RequestLine:
    pr_no: str
    pr_line: int
    created_at: datetime
    requester: str
    department: str
    plant_code: str
    supplied_item_code: str | None
    description: str
    quantity: Decimal
    uom: str
    estimated_unit_price: Decimal | None
    need_by_date: date
    urgency: str = "Normal"
    database_id: int | None = None

    @property
    def key(self) -> tuple[str, int]:
        return self.pr_no, self.pr_line


@dataclass
class ItemMatch:
    item: Any | None
    score: Decimal
    candidates: list[tuple[str, Decimal]] = field(default_factory=list)
    method: str = "unmatched"


@dataclass
class Requirement:
    ordinal: int
    lines: list[RequestLine]
    item: Any | None
    plant_code: str | None
    match: ItemMatch | None
    requirement_date: date
    first_created_at: datetime
    need_by_date: date
    quantity: Decimal
    is_emergency: bool
    group_id: str = ""
    unit_price: Decimal | None = None
    value: Decimal | None = None
    price_source: str | None = None
    history_evidence: dict[str, Any] = field(default_factory=dict)
    contract_evidence: dict[str, Any] = field(default_factory=dict)
    decision: str | None = None
    route: str | None = None
    clause: str | None = None
    supplier: Any | None = None
    contract: Any | None = None
    suggested_suppliers: list[Any] = field(default_factory=list)
    reason: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    purchase_order_group: str | None = None
    non_processed_reason: str | None = None


@dataclass
class ProcurementRunResult:
    run_id: str
    policy_version: str
    requirements: list[Requirement]
    po_groups: dict[str, list[Requirement]]
    skipped_lines: list[tuple[RequestLine, str]] = field(default_factory=list)
