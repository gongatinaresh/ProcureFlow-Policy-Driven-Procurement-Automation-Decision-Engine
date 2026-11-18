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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class Contract(TimestampMixin, Base):
    __tablename__ = "contracts"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    supplier_id: Mapped[str] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="RESTRICT"), nullable=False, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_cap: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    baseline_consumed_value: Mapped[Decimal] = mapped_column(
        Numeric(16, 2), nullable=False, default=Decimal("0")
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False, default="register")
    source_reference: Mapped[str | None] = mapped_column(String(512), nullable=True)
    source_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    verified: Mapped[bool] = mapped_column(default=False, nullable=False)
    review_status: Mapped[str] = mapped_column(String(32), nullable=False, default="pending")
    lines: Mapped[list["ContractLine"]] = relationship(
        back_populates="contract", cascade="all, delete-orphan"
    )
    allocations: Mapped[list["ContractAllocation"]] = relationship(back_populates="contract")

    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_contract_term_valid"),
        CheckConstraint("value_cap >= 0", name="ck_contract_cap_nonnegative"),
        CheckConstraint(
            "baseline_consumed_value >= 0",
            name="ck_contract_consumed_nonnegative",
        ),
        Index("ix_contract_term", "start_date", "end_date"),
    )


class ContractLine(TimestampMixin, Base):
    __tablename__ = "contract_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="CASCADE"), nullable=False
    )
    item_code: Mapped[str] = mapped_column(
        ForeignKey("items.item_code", ondelete="RESTRICT"), nullable=False
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    uom: Mapped[str] = mapped_column(String(24), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    contract: Mapped[Contract] = relationship(back_populates="lines")

    __table_args__ = (
        UniqueConstraint("contract_id", "item_code", name="uq_contract_item"),
        CheckConstraint("rate >= 0", name="ck_contract_rate_nonnegative"),
        Index("ix_contract_line_item", "item_code", "contract_id"),
    )


class ContractAllocation(TimestampMixin, Base):
    __tablename__ = "contract_allocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    contract_id: Mapped[int] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=False
    )
    decision_run_id: Mapped[int] = mapped_column(
        ForeignKey("decision_runs.id", ondelete="RESTRICT"), nullable=False
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_requirements.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="reserved")
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    contract: Mapped[Contract] = relationship(back_populates="allocations")

    __table_args__ = (
        UniqueConstraint("decision_run_id", "requirement_id", name="uq_allocation_run_requirement"),
        CheckConstraint("amount > 0", name="ck_contract_allocation_positive"),
        Index("ix_contract_allocation_contract_status", "contract_id", "status"),
    )
