"""Store financial values as fixed-scale NUMERIC instead of binary floats.

Revision ID: 0009_decimal_money_columns
Revises: 0008_auto_halt_trip
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0009_decimal_money_columns"
down_revision: Union[str, None] = "0008_auto_halt_trip"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_MONEY = sa.Numeric(28, 8)


def upgrade() -> None:
    # batch_alter_table keeps this migration usable on SQLite (the test/development
    # database) while emitting ALTER COLUMN on PostgreSQL.
    changes = {
        "transaction": ("quantity", "price"),
        "tradeorder": ("quantity", "filled_quantity", "filled_avg_price"),
        "risksetting": ("daily_loss_limit_pct",),
        "equitysnapshot": ("opening_equity",),
        "alert": ("threshold_value",),
        "backtestrun": ("initial_capital", "expectancy", "max_drawdown_pct", "total_return_pct", "final_equity"),
    }
    for table, columns in changes.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, type_=_MONEY, existing_type=sa.Float())
    with op.batch_alter_table("equitysnapshot") as batch:
        batch.create_unique_constraint(
            "uq_equitysnapshot_user_date", ["user_id", "snapshot_date"]
        )


def downgrade() -> None:
    with op.batch_alter_table("equitysnapshot") as batch:
        batch.drop_constraint("uq_equitysnapshot_user_date", type_="unique")
    changes = {
        "transaction": ("quantity", "price"),
        "tradeorder": ("quantity", "filled_quantity", "filled_avg_price"),
        "risksetting": ("daily_loss_limit_pct",),
        "equitysnapshot": ("opening_equity",),
        "alert": ("threshold_value",),
        "backtestrun": ("initial_capital", "expectancy", "max_drawdown_pct", "total_return_pct", "final_equity"),
    }
    for table, columns in changes.items():
        with op.batch_alter_table(table) as batch:
            for column in columns:
                batch.alter_column(column, type_=sa.Float(), existing_type=_MONEY)
