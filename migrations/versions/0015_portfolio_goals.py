"""Add persisted return and drawdown objectives.

Revision ID: 0015_portfolio_goals
Revises: 0014_paper_automations
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_portfolio_goals"
down_revision: Union[str, None] = "0014_paper_automations"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("portfoliogoal", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False, unique=True),
        sa.Column("target_annual_return_pct", sa.Numeric(28, 8), nullable=False),
        sa.Column("max_drawdown_pct", sa.Numeric(28, 8), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_portfoliogoal_user_id", "portfoliogoal", ["user_id"])


def downgrade() -> None:
    op.drop_table("portfoliogoal")
