"""Record the signal's confidence on the audit row.

Confidence is the one piece of signal metadata that cannot be recovered from
the order: it decides whether the trade was allowed through the gate, so the
scorecard needs it to answer "is min_trade_confidence calibrated?". Nullable
because rows written before this migration genuinely have no value — a 0.0
default would be a fabricated reading, not a missing one.

Revision ID: 0005_audit_confidence
Revises: 0004_manual_halt
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005_audit_confidence"
down_revision: Union[str, None] = "0004_manual_halt"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "tradeauditlog", sa.Column("confidence", sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("tradeauditlog", "confidence")
