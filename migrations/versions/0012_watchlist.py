"""Add per-user research watchlists.

Revision ID: 0012_watchlist
Revises: 0011_account_risk_limits
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_watchlist"
down_revision: Union[str, None] = "0011_account_risk_limits"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "watchlistitem",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("user_id", "symbol", name="uq_watchlist_user_symbol"),
    )
    op.create_index("ix_watchlistitem_user_id", "watchlistitem", ["user_id"])
    op.create_index("ix_watchlistitem_symbol", "watchlistitem", ["symbol"])


def downgrade() -> None:
    op.drop_index("ix_watchlistitem_symbol", table_name="watchlistitem")
    op.drop_index("ix_watchlistitem_user_id", table_name="watchlistitem")
    op.drop_table("watchlistitem")
