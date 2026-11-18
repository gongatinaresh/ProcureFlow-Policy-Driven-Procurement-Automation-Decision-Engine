from decimal import Decimal

from sqlalchemy import Index, JSON, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database.base import Base, TimestampMixin


class Item(TimestampMixin, Base):
    __tablename__ = "items"

    item_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    description: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    uom: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    item_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    standard_price: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)

    __table_args__ = (
        Index("ix_items_category_type", "category", "item_type"),
    )


class Supplier(TimestampMixin, Base):
    __tablename__ = "suppliers"

    supplier_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    city: Mapped[str] = mapped_column(String(100), nullable=False)
    categories: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    status: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    on_time_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    quality_rating: Mapped[Decimal] = mapped_column(Numeric(4, 2), nullable=False)


class Plant(TimestampMixin, Base):
    __tablename__ = "plants"

    plant_code: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
