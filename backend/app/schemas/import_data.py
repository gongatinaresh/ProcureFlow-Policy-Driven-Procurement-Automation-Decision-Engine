from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class CSVRow(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ItemMasterRow(CSVRow):
    item_code: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=255)
    uom: str = Field(min_length=1, max_length=24)
    category: str = Field(min_length=1, max_length=100)
    item_type: str = Field(min_length=1, max_length=50)
    standard_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class SupplierMasterRow(CSVRow):
    supplier_id: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)
    city: str = Field(min_length=1, max_length=100)
    categories: list[str]
    status: Literal["Active", "Blocked", "Inactive"]
    on_time_pct: Decimal = Field(ge=0, le=100, max_digits=5, decimal_places=2)
    quality_rating: Decimal = Field(ge=0, le=5, max_digits=4, decimal_places=2)

    @field_validator("categories", mode="before")
    @classmethod
    def split_categories(cls, value: str | list[str]) -> list[str]:
        values = value.split(";") if isinstance(value, str) else value
        categories = [category.strip() for category in values if category.strip()]
        if not categories:
            raise ValueError("at least one supplier category is required")
        return categories


class PlantMasterRow(CSVRow):
    plant: str = Field(min_length=1, max_length=32)
    name: str = Field(min_length=1, max_length=255)


class PurchaseHistoryRow(CSVRow):
    po_number: str = Field(min_length=1, max_length=64)
    po_date: date
    supplier_id: str = Field(min_length=1, max_length=32)
    item_code: str = Field(min_length=1, max_length=32)
    quantity: Decimal = Field(gt=0, max_digits=16, decimal_places=3)
    uom: str = Field(min_length=1, max_length=24)
    unit_price: Decimal = Field(ge=0, max_digits=14, decimal_places=2)
    plant: str = Field(min_length=1, max_length=32)
    delivered_on_time: bool

    @field_validator("delivered_on_time", mode="before")
    @classmethod
    def parse_boolean(cls, value: str | bool) -> bool:
        if isinstance(value, bool):
            return value
        normalized = value.strip().casefold()
        if normalized in {"true", "1", "yes"}:
            return True
        if normalized in {"false", "0", "no"}:
            return False
        raise ValueError("expected True/False, 1/0, or yes/no")


class ContractRegisterRow(CSVRow):
    contract_id: str = Field(min_length=1, max_length=64)
    supplier_id: str = Field(min_length=1, max_length=32)
    supplier_name: str = Field(min_length=1, max_length=255)
    start_date: date
    end_date: date
    value_cap: Decimal = Field(ge=0, max_digits=16, decimal_places=2)
    consumed_value: Decimal = Field(ge=0, max_digits=16, decimal_places=2)

    @model_validator(mode="after")
    def validate_term(self) -> "ContractRegisterRow":
        if self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        if self.consumed_value > self.value_cap:
            raise ValueError("consumed_value cannot exceed value_cap")
        return self


class ContractLineRow(CSVRow):
    contract_id: str = Field(min_length=1, max_length=64)
    item_code: str = Field(min_length=1, max_length=32)
    description: str = Field(min_length=1, max_length=255)
    uom: str = Field(min_length=1, max_length=24)
    rate: Decimal = Field(ge=0, max_digits=14, decimal_places=2)


class ImportFileSummary(BaseModel):
    file: str
    rows_seen: int = 0
    imported: int = 0
    updated: int = 0
    skipped: int = 0
    rejected: int = 0
    errors: list[str] = Field(default_factory=list)


class MasterDataImportSummary(BaseModel):
    source_directory: str
    files: list[ImportFileSummary]

    @property
    def has_rejections(self) -> bool:
        return any(file.rejected > 0 for file in self.files)


class ContractPDFResult(BaseModel):
    file: str
    contract_id: str | None = None
    imported: int = 0
    skipped: int = 0
    rejected: int = 0
    missing_from_register: bool = False
    review_status: Literal["verified", "pending_manual_review", "mismatch"] | None = None
    discrepancies: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class ContractPDFImportSummary(BaseModel):
    source_directory: str
    pdfs_seen: int = 0
    imported: int = 0
    skipped: int = 0
    rejected: int = 0
    contracts_missing_from_register: list[str] = Field(default_factory=list)
    registered_contracts_without_pdf: list[str] = Field(default_factory=list)
    documents: list[ContractPDFResult] = Field(default_factory=list)
