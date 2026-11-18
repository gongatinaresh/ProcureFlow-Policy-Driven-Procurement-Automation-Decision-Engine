from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class PurchaseOrder(TimestampMixin, Base):
    __tablename__ = "purchase_orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    po_number: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="RESTRICT"), nullable=False
    )
    plant_code: Mapped[str] = mapped_column(
        ForeignKey("plants.plant_code", ondelete="RESTRICT"), nullable=False
    )
    route: Mapped[str] = mapped_column(String(48), nullable=False)
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="draft", index=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    lines: Mapped[list["PurchaseOrderLine"]] = relationship(
        back_populates="purchase_order", cascade="all, delete-orphan"
    )

    __table_args__ = (Index("ix_purchase_order_supplier_plant_route", "supplier_id", "plant_code", "route"),)


class PurchaseOrderLine(TimestampMixin, Base):
    __tablename__ = "purchase_order_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    purchase_order_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_requirements.id", ondelete="RESTRICT"), nullable=False
    )
    item_code: Mapped[str] = mapped_column(
        ForeignKey("items.item_code", ondelete="RESTRICT"), nullable=False
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    uom: Mapped[str] = mapped_column(String(24), nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_value_inr: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    purchase_order: Mapped[PurchaseOrder] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("purchase_order_id", "requirement_id", name="uq_po_requirement"),
        CheckConstraint("quantity > 0", name="ck_po_line_quantity_positive"),
        CheckConstraint("unit_price >= 0", name="ck_po_line_price_nonnegative"),
        CheckConstraint("line_value_inr >= 0", name="ck_po_line_value_nonnegative"),
    )
