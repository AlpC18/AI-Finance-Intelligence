"""Add account-level order and concentration limits.

Revision ID: 0011_account_risk_limits
Revises: 0010_order_brackets
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_account_risk_limits"
down_revision: Union[str, None] = "0010_order_brackets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("risksetting") as batch:
        batch.add_column(sa.Column("max_daily_trades", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("max_position_weight_pct", sa.Numeric(28, 8), nullable=False, server_default="0"))
        batch.add_column(sa.Column("require_protective_stop", sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade() -> None:
    with op.batch_alter_table("risksetting") as batch:
        batch.drop_column("require_protective_stop")
        batch.drop_column("max_position_weight_pct")
        batch.drop_column("max_daily_trades")
