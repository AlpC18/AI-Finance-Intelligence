"""Execution reconciliation + risk: orders, audit log, risk settings, equity snapshots.

Revision ID: 0003_execution_and_risk
Revises: 0002_broker_credentials
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0003_execution_and_risk"
down_revision: Union[str, None] = "0002_broker_credentials"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "tradeorder",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(), nullable=False),
        sa.Column("broker_order_id", sa.String(), nullable=False),
        sa.Column("symbol", sa.String(), nullable=False),
        sa.Column("side", sa.String(), nullable=False),
        sa.Column("quantity", sa.Float(), nullable=False),
        sa.Column("order_type", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("filled_quantity", sa.Float(), nullable=False),
        sa.Column("filled_avg_price", sa.Float(), nullable=True),
        sa.Column("reconciled", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index("ix_tradeorder_user_id", "tradeorder", ["user_id"])
    op.create_index("ix_tradeorder_broker_order_id", "tradeorder", ["broker_order_id"])
    op.create_index("ix_tradeorder_symbol", "tradeorder", ["symbol"])
    op.create_index("ix_tradeorder_status", "tradeorder", ["status"])
    op.create_index("ix_tradeorder_reconciled", "tradeorder", ["reconciled"])
    op.create_index("ix_tradeorder_broker", "tradeorder", ["broker"])

    op.create_table(
        "tradeauditlog",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.String(), nullable=False),
        sa.Column("signal_type", sa.String(), nullable=False),
        sa.Column("execution_timestamp", sa.DateTime(), nullable=False),
        sa.Column("raw_ai_context", sa.String(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index("ix_tradeauditlog_user_id", "tradeauditlog", ["user_id"])
    op.create_index("ix_tradeauditlog_order_id", "tradeauditlog", ["order_id"])
    op.create_index(
        "ix_tradeauditlog_execution_timestamp", "tradeauditlog", ["execution_timestamp"]
    )

    op.create_table(
        "risksetting",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("daily_loss_limit_pct", sa.Float(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index("ix_risksetting_user_id", "risksetting", ["user_id"], unique=True)

    op.create_table(
        "equitysnapshot",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.String(), nullable=False),
        sa.Column("opening_equity", sa.Float(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index("ix_equitysnapshot_user_id", "equitysnapshot", ["user_id"])
    op.create_index("ix_equitysnapshot_snapshot_date", "equitysnapshot", ["snapshot_date"])


def downgrade() -> None:
    op.drop_table("equitysnapshot")
    op.drop_table("risksetting")
    op.drop_table("tradeauditlog")
    op.drop_table("tradeorder")
