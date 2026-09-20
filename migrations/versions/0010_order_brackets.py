"""Persist broker-side protective bracket prices on local orders.

Revision ID: 0010_order_brackets
Revises: 0009_decimal_money_columns
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0010_order_brackets"
down_revision: Union[str, None] = "0009_decimal_money_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("tradeorder") as batch:
        batch.add_column(sa.Column("stop_loss", sa.Numeric(28, 8), nullable=True))
        batch.add_column(sa.Column("take_profit", sa.Numeric(28, 8), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tradeorder") as batch:
        batch.drop_column("take_profit")
        batch.drop_column("stop_loss")
