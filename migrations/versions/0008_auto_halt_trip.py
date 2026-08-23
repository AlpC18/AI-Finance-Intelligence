"""Record the day the automatic drawdown halt fired.

The daily-loss limit used to be evaluated only when something asked - an execute
attempt or a status read - so an account with a resting order and nobody
watching could run straight through its limit. A scheduled sweep now evaluates
it on a timer and flattens on trip.

That sweep needs to know it has already acted. A date is exactly the right
granularity: drawdown is a daily measure that resets with the opening equity
snapshot, so the date makes the trip idempotent within a day (the sweep must not
re-flatten and re-notify every five minutes) while leaving tomorrow free to trip
on its own.

Deliberately NOT reusing ``manual_halt``: that column carries operator intent,
and overloading it would make "resume trading" indistinguishable from "clear an
automatic trip".

Revision ID: 0008_auto_halt_trip
Revises: 0007_backtest_runs
Create Date: 2026-08-23
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_auto_halt_trip"
down_revision: Union[str, None] = "0007_backtest_runs"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: existing accounts have never tripped, and NULL says that
    # honestly where a default date would claim a trip that never happened.
    op.add_column(
        "risksetting", sa.Column("auto_halt_tripped_on", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("risksetting", "auto_halt_tripped_on")
