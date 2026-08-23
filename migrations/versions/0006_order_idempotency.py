"""Duplicate-order protection: a de-duplication token, unique per user.

Without this, a double-clicked button or a client-side retry after a timeout
opens two real positions - both technically valid, so reconciliation cannot
tell them apart afterwards. The uniqueness is scoped per user, not global:
two users must be free to choose the same key, and a global constraint would
leak one user's key space into another's.

Nullable because rows written before this migration have no token, and SQL
treats NULLs as distinct, so any number of legacy rows coexist under the
unique index.

Revision ID: 0006_order_idempotency
Revises: 0005_audit_confidence
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006_order_idempotency"
down_revision: Union[str, None] = "0005_audit_confidence"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tradeorder", sa.Column("client_order_id", sa.String(), nullable=True)
    )
    op.create_index(
        "ix_tradeorder_client_order_id", "tradeorder", ["client_order_id"]
    )
    op.create_index(
        "uq_tradeorder_user_client_order_id",
        "tradeorder",
        ["user_id", "client_order_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_tradeorder_user_client_order_id", table_name="tradeorder")
    op.drop_index("ix_tradeorder_client_order_id", table_name="tradeorder")
    op.drop_column("tradeorder", "client_order_id")
