"""Add consumer credentials and enable TimescaleDB where available.

Revision ID: 0018_consumer_keys_timescale
Revises: 0017_activity_timeline
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0018_consumer_keys_timescale"
down_revision: Union[str, None] = "0017_activity_timeline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "apiconsumerkey",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("key_prefix", sa.String(), nullable=False, unique=True),
        sa.Column("key_hash", sa.String(), nullable=False, unique=True),
        sa.Column("scopes_csv", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_apiconsumerkey_user_id", "apiconsumerkey", ["user_id"])
    op.create_index("ix_apiconsumerkey_key_prefix", "apiconsumerkey", ["key_prefix"])
    op.create_index("ix_apiconsumerkey_is_active", "apiconsumerkey", ["is_active"])
    if op.get_bind().dialect.name == "postgresql":
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")


def downgrade() -> None:
    op.drop_table("apiconsumerkey")
