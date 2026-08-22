"""Add operator manual kill-switch toggle to risk settings.

Revision ID: 0004_manual_halt
Revises: 0003_execution_and_risk
Create Date: 2026-08-21
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0004_manual_halt"
down_revision: Union[str, None] = "0003_execution_and_risk"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "risksetting",
        sa.Column("manual_halt", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("risksetting", "manual_halt")
