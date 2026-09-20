"""Add exact price targets to watchlist items.

Revision ID: 0016_watchlist_price_targets
Revises: 0015_portfolio_goals
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0016_watchlist_price_targets"
down_revision: Union[str, None] = "0015_portfolio_goals"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "watchlistitem",
        sa.Column("target_price", sa.Numeric(precision=28, scale=8), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("watchlistitem", "target_price")
