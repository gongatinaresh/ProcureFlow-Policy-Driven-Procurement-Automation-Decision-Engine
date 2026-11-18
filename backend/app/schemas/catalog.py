from decimal import Decimal

from pydantic import Field

from app.schemas.common import ORMModel


class ItemCreate(ORMModel):
    item_code: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=255)
    uom: str = Field(min_length=1, max_length=24)
    category: str = Field(min_length=1, max_length=100)
    item_type: str = Field(min_length=1, max_length=50)
    standard_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class ItemResponse(ItemCreate):
    pass


class SupplierCreate(ORMModel):
    supplier_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    categories: list[str]
    status: str = Field(min_length=1, max_length=24)
    on_time_pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    quality_rating: Decimal = Field(ge=0, le=5, max_digits=4, decimal_places=2)


class SupplierResponse(SupplierCreate):
    pass


class PlantResponse(ORMModel):
    plant_code: str
    name: str
