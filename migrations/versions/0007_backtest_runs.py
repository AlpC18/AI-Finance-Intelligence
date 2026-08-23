"""Persisted backtest history: strategy runs kept instead of discarded.

Every simulation was previously computed, returned once and thrown away, so a
result worth acting on could only be seen again by re-running it - against
whatever bars existed by then, which makes it a different result.

The headline metrics are columns as well as living inside ``report_json``. That
duplication is intentional and safe because a run is immutable: listing and
ranking history must not cost a JSON parse per row, while the equity curve and
the trade list are only ever read whole, one run at a time.

Revision ID: 0007_backtest_runs
Revises: 0006_order_idempotency
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
import sqlmodel
from alembic import op

revision: str = "0007_backtest_runs"
down_revision: Union[str, None] = "0006_order_idempotency"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "backtestrun",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("symbol", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("strategy", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        # The request, so a stored number stays attributable to what produced it.
        sa.Column("period", sqlmodel.sql.sqltypes.AutoString(), nullable=False),
        sa.Column("initial_capital", sa.Float(), nullable=False),
        sa.Column("rsi_buy", sa.Float(), nullable=False),
        sa.Column("rsi_sell", sa.Float(), nullable=False),
        sa.Column("warmup", sa.Integer(), nullable=False),
        sa.Column("bars", sa.Integer(), nullable=False),
        sa.Column("trades", sa.Integer(), nullable=False),
        sa.Column("hit_rate_pct", sa.Float(), nullable=False),
        # Nullable: undefined when a run had no losing trade. NULL is "cannot be
        # ranked", which is not the same as a profit factor of zero.
        sa.Column("profit_factor", sa.Float(), nullable=True),
        sa.Column("expectancy", sa.Float(), nullable=False),
        sa.Column("max_drawdown_pct", sa.Float(), nullable=False),
        sa.Column("sharpe_ratio", sa.Float(), nullable=False),
        sa.Column("total_return_pct", sa.Float(), nullable=False),
        sa.Column("final_equity", sa.Float(), nullable=False),
        sa.Column("report_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    # user_id + created_at carry the list query ("my runs, newest first");
    # symbol carries the filtered variant.
    op.create_index("ix_backtestrun_user_id", "backtestrun", ["user_id"])
    op.create_index("ix_backtestrun_symbol", "backtestrun", ["symbol"])
    op.create_index("ix_backtestrun_created_at", "backtestrun", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_backtestrun_created_at", table_name="backtestrun")
    op.drop_index("ix_backtestrun_symbol", table_name="backtestrun")
    op.drop_index("ix_backtestrun_user_id", table_name="backtestrun")
    op.drop_table("backtestrun")
