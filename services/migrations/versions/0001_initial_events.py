"""initial events and commands tables

Revision ID: 0001
Revises:
Create Date: 2026-08-01
"""

from alembic import op
import sqlalchemy as sa

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "events",
        sa.Column("sequence", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(), nullable=False, unique=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("stream_id", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column("occurred_at", sa.String(), nullable=False),
        sa.Column("payload", sa.Text(), nullable=False),
    )
    op.create_index("idx_events_stream", "events", ["stream_id", "sequence"])
    op.create_table(
        "commands",
        sa.Column("command_id", sa.String(), primary_key=True),
        sa.Column("command_type", sa.String(), nullable=False),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=False, unique=True),
        sa.Column("state", sa.String(), nullable=False),
        sa.Column("requested_at", sa.String(), nullable=False),
        sa.Column("accepted_at", sa.String(), nullable=True),
        sa.Column("rejected_reason", sa.String(), nullable=True),
        sa.Column("payload", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("commands")
    op.drop_index("idx_events_stream", table_name="events")
    op.drop_table("events")
