"""store policy document and source metadata

Revision ID: 0f48d8e56843
Revises: c6b7883618ae
Create Date: 2026-11-17 17:04:04.204687
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0f48d8e56843"
down_revision: Union[str, None] = "c6b7883618ae"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "policy_versions", sa.Column("source_sha256", sa.String(length=64), nullable=True)
    )
    op.add_column("policy_versions", sa.Column("extracted_text", sa.Text(), nullable=True))
    op.add_column("policy_versions", sa.Column("policy_document", sa.JSON(), nullable=True))
    with op.batch_alter_table("policy_versions") as batch_op:
        batch_op.add_column(sa.Column("parent_policy_version_id", sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            "fk_policy_versions_parent_policy_version_id",
            "policy_versions",
            ["parent_policy_version_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def downgrade() -> None:
    with op.batch_alter_table("policy_versions") as batch_op:
        batch_op.drop_constraint(
            "fk_policy_versions_parent_policy_version_id",
            type_="foreignkey",
        )
        batch_op.drop_column("parent_policy_version_id")
    op.drop_column("policy_versions", "policy_document")
    op.drop_column("policy_versions", "extracted_text")
    op.drop_column("policy_versions", "source_sha256")
