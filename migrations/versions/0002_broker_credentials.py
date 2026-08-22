"""Broker credentials (encrypted-at-rest API keys for trade execution).

Revision ID: 0002_broker_credentials
Revises: 0001_initial_ledger
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002_broker_credentials"
down_revision: Union[str, None] = "0001_initial_ledger"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "brokercredential",
        sa.Column("id", sa.Integer(), primary_key=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("broker", sa.String(), nullable=False),
        sa.Column("api_key_enc", sa.String(), nullable=False),
        sa.Column("api_secret_enc", sa.String(), nullable=False),
        sa.Column("paper", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["user.id"]),
    )
    op.create_index(
        "ix_brokercredential_user_id", "brokercredential", ["user_id"], unique=False
    )
    op.create_index(
        "ix_brokercredential_broker", "brokercredential", ["broker"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_brokercredential_broker", table_name="brokercredential")
    op.drop_index("ix_brokercredential_user_id", table_name="brokercredential")
    op.drop_table("brokercredential")
