"""drop conversations.summary_msg_count (single seq cursor)

Retires the legacy POSITIONAL folding cursor. summary_msg_seq is now the only
cursor: message seq values are contiguous from 0 (no edits/deletes in this
product), so the seq cursor doubles as a positional index into the seq-ordered
history -- no second column is needed.

Revision ID: c3d4e5f60718
Revises: b2c3d4e5f6a7
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa

revision = "c3d4e5f60718"
down_revision = "b2c3d4e5f6a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("conversations", "summary_msg_count")


def downgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("summary_msg_count", sa.Integer(), nullable=False, server_default="0"),
    )
