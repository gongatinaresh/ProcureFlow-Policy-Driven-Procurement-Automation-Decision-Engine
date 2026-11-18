from decimal import Decimal
from typing import Any

from pydantic import Field

from app.schemas.common import ORMModel


class DecisionResponse(ORMModel):
    id: int
    decision_run_id: int
    requirement_id: int
    clause: str
    outcome: str
    route: str
    supplier_id: str | None
    unit_price: Decimal | None
    group_value_inr: Decimal
    price_source: str | None
    suggested_supplier_ids: list[str]
    evidence: dict[str, Any]
    reason: str
    overridden: bool
    override_reason: str | None


class DecisionOverrideRequest(ORMModel):
    route: str = Field(min_length=1, max_length=48)
    reason: str = Field(min_length=1, max_length=4000)
