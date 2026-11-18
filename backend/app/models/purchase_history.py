from datetime import date
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class PurchaseHistory(TimestampMixin, Base):
    __tablename__ = "purchase_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False)
    po_date: Mapped[date] = mapped_column(Date, nullable=False)
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="RESTRICT"), nullable=False
    )
    item_code: Mapped[str] = mapped_column(
        ForeignKey("items.item_code", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    uom: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    plant_code: Mapped[str] = mapped_column(
        ForeignKey("plants.plant_code", ondelete="RESTRICT"), nullable=False
    )
    delivered_on_time: Mapped[bool] = mapped_column(nullable=False)

    __table_args__ = (
        UniqueConstraint("po_number", "item_code", name="uq_purchase_history_po_item"),
        CheckConstraint("quantity > 0", name="ck_history_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_history_price_nonnegative"),
        Index("ix_history_item_date", "item_code", "po_date"),
        Index("ix_history_supplier_item_date", "supplier_id", "item_code", "po_date"),
    )
