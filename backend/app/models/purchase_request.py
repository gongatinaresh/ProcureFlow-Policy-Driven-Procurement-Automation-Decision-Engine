from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class PurchaseRequirement(TimestampMixin, Base):
    __tablename__ = "purchase_requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("decision_runs.id", ondelete="CASCADE"), nullable=True, index=True
    )
    item_code: Mapped[str | None] = mapped_column(
        ForeignKey("items.item_code", ondelete="RESTRICT"), nullable=True
    )
    plant_code: Mapped[str | None] = mapped_column(
        ForeignKey("plants.plant_code", ondelete="RESTRICT"), nullable=True
    )
    requirement_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    first_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    need_by_date: Mapped[date] = mapped_column(Date, nullable=False)
    total_quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric(16, 2), nullable=True)
    is_emergency: Mapped[bool] = mapped_column(default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False, index=True)
    lines: Mapped[list["PRLine"]] = relationship(back_populates="requirement")
    decisions: Mapped[list["Decision"]] = relationship(back_populates="requirement")

    __table_args__ = (
        CheckConstraint("total_quantity > 0", name="ck_requirement_quantity_positive"),
        UniqueConstraint("decision_run_id", "group_id", name="uq_requirement_run_group"),
        Index("ix_requirement_item_plant_date", "item_code", "plant_code", "requirement_date"),
    )


class PRLine(TimestampMixin, Base):
    __tablename__ = "pr_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    pr_no: Mapped[str] = mapped_column(String(64), nullable=False)
    pr_line: Mapped[int] = mapped_column(nullable=False)
    created_at_source: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    requester: Mapped[str] = mapped_column(String(255), nullable=False)
    department: Mapped[str] = mapped_column(String(100), nullable=False)
    plant_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    matched_plant_code: Mapped[str | None] = mapped_column(
        ForeignKey("plants.plant_code", ondelete="RESTRICT"), nullable=True
    )
    supplied_item_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    resolved_item_code: Mapped[str | None] = mapped_column(
        ForeignKey("items.item_code", ondelete="RESTRICT"), nullable=True
    )
    quantity: Mapped[Decimal] = mapped_column(Numeric(16, 3), nullable=False)
    uom: Mapped[str] = mapped_column(String(24), nullable=False)
    estimated_unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    need_by_date: Mapped[date] = mapped_column(Date, nullable=False)
    urgency: Mapped[str] = mapped_column(String(24), nullable=False, default="Normal")
    requirement_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_requirements.id", ondelete="SET NULL"), nullable=True, index=True
    )
    requirement: Mapped[PurchaseRequirement | None] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("pr_no", "pr_line", name="uq_pr_line_number"),
        CheckConstraint("pr_line > 0", name="ck_pr_line_positive"),
        CheckConstraint("quantity > 0", name="ck_pr_quantity_positive"),
        CheckConstraint(
            "estimated_unit_price IS NULL OR estimated_unit_price >= 0",
            name="ck_pr_estimated_price_nonnegative",
        ),
        Index("ix_pr_lines_item_created", "resolved_item_code", "created_at_source"),
        Index("ix_pr_lines_plant_need_by", "plant_code", "need_by_date"),
    )
