"""Add isolated paper accounts and simulated fills.

Revision ID: 0013_paper_trading
Revises: 0012_watchlist
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0013_paper_trading"
down_revision: Union[str, None] = "0012_watchlist"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table("paperaccount", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False, unique=True),
        sa.Column("currency", sa.String(), nullable=False), sa.Column("cash", sa.Numeric(28, 8), nullable=False),
        sa.Column("starting_cash", sa.Numeric(28, 8), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False), sa.Column("updated_at", sa.DateTime(), nullable=False))
    op.create_index("ix_paperaccount_user_id", "paperaccount", ["user_id"])
    op.create_table("paperfill", sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False), sa.Column("side", sa.String(), nullable=False),
        sa.Column("quantity", sa.Numeric(28, 8), nullable=False), sa.Column("reference_price", sa.Numeric(28, 8), nullable=False),
        sa.Column("fill_price", sa.Numeric(28, 8), nullable=False), sa.Column("fee", sa.Numeric(28, 8), nullable=False),
        sa.Column("slippage_bps", sa.Numeric(28, 8), nullable=False), sa.Column("created_at", sa.DateTime(), nullable=False))
    op.create_index("ix_paperfill_user_id", "paperfill", ["user_id"])
    op.create_index("ix_paperfill_symbol", "paperfill", ["symbol"])
    op.create_index("ix_paperfill_created_at", "paperfill", ["created_at"])


def downgrade() -> None:
    op.drop_table("paperfill")
    op.drop_table("paperaccount")
