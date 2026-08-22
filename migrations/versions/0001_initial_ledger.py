"""Initial schema: users, transaction ledger, alerts (ledger-driven overhaul).

Revision ID: 0001_initial_ledger
Revises:
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001_initial_ledger"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("hashed_password", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_user_email", "user", ["email"], unique=True)

    op.create_table(
        "transaction",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_transaction_user_id", "transaction", ["user_id"])
    op.create_index("ix_transaction_symbol", "transaction", ["symbol"])
    op.create_index("ix_transaction_action", "transaction", ["action"])
    op.create_index("ix_transaction_timestamp", "transaction", ["timestamp"])

    op.create_table(
        "alert",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("condition_type", sa.String(), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("last_triggered_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_alert_user_id", "alert", ["user_id"])
    op.create_index("ix_alert_symbol", "alert", ["symbol"])
    op.create_index("ix_alert_is_active", "alert", ["is_active"])


def downgrade() -> None:
    op.drop_table("alert")
    op.drop_table("transaction")
    op.drop_index("ix_user_email", table_name="user")
    op.drop_table("user")
