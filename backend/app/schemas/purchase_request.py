from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas.common import ORMModel


class PRLineCreate(ORMModel):
    pr_no: str = Field(min_length=1, max_length=64)
    pr_line: int = Field(gt=0)
    created_at_source: datetime
    requester: str = Field(min_length=1, max_length=255)
    department: str = Field(min_length=1, max_length=100)
    plant_code: str = Field(min_length=1, max_length=32)
    supplied_item_code: str | None = Field(default=None, max_length=32)
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)
    uom: str = Field(min_length=1, max_length=24)
    estimated_unit_price: Decimal | None = Field(
        default=None, ge=0, max_digits=14, decimal_places=2
    )
    need_by_date: date
    urgency: Literal["Normal", "Emergency"] = "Normal"


class PRLineResponse(ORMModel):
    id: int
    pr_no: str
    pr_line: int
    plant_code: str
    supplied_item_code: str | None
    resolved_item_code: str | None
    description: str
    quantity: Decimal
    uom: str
    estimated_unit_price: Decimal | None
    need_by_date: date
    urgency: str
    requirement_id: int | None


class PurchaseRequirementResponse(ORMModel):
    id: int
    group_id: str
    item_code: str | None
    plant_code: str | None
    requirement_date: date
    total_quantity: Decimal
    estimated_value: Decimal | None
    is_emergency: bool
    status: str
