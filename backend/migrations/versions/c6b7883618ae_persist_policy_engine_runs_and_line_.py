"""persist policy engine runs and line decisions

Revision ID: c6b7883618ae
Revises: 19a759929f1b
Create Date: 2026-11-17 16:34:20.439920
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c6b7883618ae'
down_revision: Union[str, None] = '19a759929f1b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('decision_lines',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('decision_id', sa.Integer(), nullable=False),
    sa.Column('pr_line_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['decision_id'], ['decisions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['pr_line_id'], ['pr_lines.id'], ondelete='RESTRICT'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_decision_lines_decision', 'decision_lines', ['decision_id'], unique=False)
    op.create_index('ix_decision_lines_pr_line', 'decision_lines', ['pr_line_id'], unique=False)
    op.create_index('uq_decision_pr_line', 'decision_lines', ['decision_id', 'pr_line_id'], unique=True)
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.add_column(sa.Column('purchase_order_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_decisions_purchase_order_id_purchase_orders',
            'purchase_orders',
            ['purchase_order_id'],
            ['id'],
            ondelete='SET NULL',
        )
    op.create_index(op.f('ix_decisions_purchase_order_id'), 'decisions', ['purchase_order_id'], unique=False)
    op.create_index('ix_decisions_run_requirement', 'decisions', ['decision_run_id', 'requirement_id'], unique=True)
    with op.batch_alter_table(
        'purchase_requirements',
        naming_convention={'uq': 'uq_%(table_name)s_%(column_0_name)s'},
    ) as batch_op:
        batch_op.drop_constraint('uq_purchase_requirements_group_id', type_='unique')
        batch_op.add_column(sa.Column('decision_run_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_purchase_requirements_decision_run_id_decision_runs',
            'decision_runs',
            ['decision_run_id'],
            ['id'],
            ondelete='CASCADE',
        )
        batch_op.create_unique_constraint(
            'uq_requirement_run_group',
            ['decision_run_id', 'group_id'],
        )
    op.create_index(op.f('ix_purchase_requirements_decision_run_id'), 'purchase_requirements', ['decision_run_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_purchase_requirements_decision_run_id'), table_name='purchase_requirements')
    with op.batch_alter_table(
        'purchase_requirements',
        naming_convention={'uq': 'uq_%(table_name)s_%(column_0_name)s'},
    ) as batch_op:
        batch_op.drop_constraint('uq_requirement_run_group', type_='unique')
        batch_op.drop_constraint(
            'fk_purchase_requirements_decision_run_id_decision_runs',
            type_='foreignkey',
        )
        batch_op.drop_column('decision_run_id')
        batch_op.create_unique_constraint(
            'uq_purchase_requirements_group_id',
            ['group_id'],
        )
    op.drop_index('ix_decisions_run_requirement', table_name='decisions')
    op.drop_index(op.f('ix_decisions_purchase_order_id'), table_name='decisions')
    with op.batch_alter_table('decisions') as batch_op:
        batch_op.drop_constraint(
            'fk_decisions_purchase_order_id_purchase_orders',
            type_='foreignkey',
        )
        batch_op.drop_column('purchase_order_id')
    op.drop_index('uq_decision_pr_line', table_name='decision_lines')
    op.drop_index('ix_decision_lines_pr_line', table_name='decision_lines')
    op.drop_index('ix_decision_lines_decision', table_name='decision_lines')
    op.drop_table('decision_lines')
