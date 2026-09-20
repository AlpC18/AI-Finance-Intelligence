"""Add guarded paper-trading automation rules.

Revision ID: 0014_paper_automations
Revises: 0013_paper_trading
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_paper_automations"
down_revision: Union[str, None] = "0013_paper_trading"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("paperautomationrule", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False), sa.Column("trigger", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 8), nullable=False), sa.Column("threshold", sa.Numeric(28, 8)),
        sa.Column("cooldown_minutes", sa.Integer(), nullable=False), sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime()), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_paperautomationrule_user_id", "paperautomationrule", ["user_id"])
    op.create_index("ix_paperautomationrule_symbol", "paperautomationrule", ["symbol"])
    op.create_index("ix_paperautomationrule_enabled", "paperautomationrule", ["enabled"])


def downgrade() -> None:
    op.drop_table("paperautomationrule")
