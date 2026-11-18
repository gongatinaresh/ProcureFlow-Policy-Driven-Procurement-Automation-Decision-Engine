from datetime import datetime
from decimal import Decimal

from app.schemas.common import ORMModel


class PurchaseOrderLineResponse(ORMModel):
    id: int
    requirement_id: int
    item_code: str
    quantity: Decimal
    uom: str
    unit_price: Decimal
    line_value_inr: Decimal


class PurchaseOrderResponse(ORMModel):
    id: int
    po_number: str
    supplier_id: str
    plant_code: str
    route: str
    contract_id: int | None
    status: str
    approved_by: str | None
    approved_at: datetime | None
