"""Add durable account activity timeline.

Revision ID: 0017_activity_timeline
Revises: 0016_watchlist_price_targets
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0017_activity_timeline"
down_revision: Union[str, None] = "0016_watchlist_price_targets"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activityevent",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("severity", sa.Integer(), nullable=False),
        sa.Column("summary", sa.String(), nullable=False),
        sa.Column("payload_json", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_activityevent_user_id", "activityevent", ["user_id"])
    op.create_index("ix_activityevent_kind", "activityevent", ["kind"])
    op.create_index("ix_activityevent_created_at", "activityevent", ["created_at"])


def downgrade() -> None:
    op.drop_table("activityevent")
