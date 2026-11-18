"""add persisted batch results for exports

Revision ID: 35c9ab7f7a20
Revises: 0f48d8e56843
Create Date: 2026-11-17 17:20:00.000000
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "35c9ab7f7a20"
down_revision: Union[str, None] = "0f48d8e56843"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "batch_runs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("input_sha256", sa.String(length=64), nullable=False),
        sa.Column("result_rows", sa.JSON(), nullable=False),
        sa.Column("statistics", sa.JSON(), nullable=False),
        sa.Column("policy_versions", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_batch_runs_input_sha256", "batch_runs", ["input_sha256"])


def downgrade() -> None:
    op.drop_index("ix_batch_runs_input_sha256", table_name="batch_runs")
    op.drop_table("batch_runs")
