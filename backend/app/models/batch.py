from typing import Any

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class BatchRun(TimestampMixin, Base):
    __tablename__ = "batch_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    input_sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    result_rows: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    statistics: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    policy_versions: Mapped[list[str]] = mapped_column(JSON, nullable=False)
