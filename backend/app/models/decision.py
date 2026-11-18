from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, JSON, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class DecisionRun(TimestampMixin, Base):
    __tablename__ = "decision_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    policy_version_id: Mapped[int] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="RESTRICT"), nullable=False
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    input_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="pending")
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    decisions: Mapped[list["Decision"]] = relationship(back_populates="run")

    __table_args__ = (Index("ix_decision_run_policy_status", "policy_version_id", "status"),)


class Decision(TimestampMixin, Base):
    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_run_id: Mapped[int] = mapped_column(
        ForeignKey("decision_runs.id", ondelete="CASCADE"), nullable=False
    )
    requirement_id: Mapped[int] = mapped_column(
        ForeignKey("purchase_requirements.id", ondelete="RESTRICT"), nullable=False
    )
    purchase_order_id: Mapped[int | None] = mapped_column(
        ForeignKey("purchase_orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("policy_rules.id", ondelete="SET NULL"), nullable=True
    )
    clause: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    route: Mapped[str] = mapped_column(String(48), nullable=False)
    supplier_id: Mapped[str | None] = mapped_column(
        ForeignKey("suppliers.supplier_id", ondelete="RESTRICT"), nullable=True
    )
    contract_id: Mapped[int | None] = mapped_column(
        ForeignKey("contracts.id", ondelete="RESTRICT"), nullable=True
    )
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)
    group_value_inr: Mapped[Decimal] = mapped_column(Numeric(16, 2), nullable=False)
    price_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    suggested_supplier_ids: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    overridden: Mapped[bool] = mapped_column(default=False, nullable=False)
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    run: Mapped[DecisionRun] = relationship(back_populates="decisions")
    requirement: Mapped["PurchaseRequirement"] = relationship(back_populates="decisions")
    purchase_order: Mapped["PurchaseOrder | None"] = relationship()

    __table_args__ = (
        Index("ix_decisions_run_requirement", "decision_run_id", "requirement_id", unique=True),
        Index("ix_decisions_requirement", "requirement_id"),
        Index("ix_decisions_run_route", "decision_run_id", "route"),
    )


class DecisionLine(TimestampMixin, Base):
    __tablename__ = "decision_lines"

    id: Mapped[int] = mapped_column(primary_key=True)
    decision_id: Mapped[int] = mapped_column(
        ForeignKey("decisions.id", ondelete="CASCADE"), nullable=False
    )
    pr_line_id: Mapped[int] = mapped_column(
        ForeignKey("pr_lines.id", ondelete="RESTRICT"), nullable=False
    )
    __table_args__ = (
        Index("ix_decision_lines_decision", "decision_id", unique=False),
        Index("ix_decision_lines_pr_line", "pr_line_id", unique=False),
        Index("uq_decision_pr_line", "decision_id", "pr_line_id", unique=True),
    )
