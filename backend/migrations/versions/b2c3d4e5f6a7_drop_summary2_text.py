"""drop conversations.summary2_text (retire the L1 secondary summary tier)

The L1 tier cost one extra LLM call per archive and was then injected into
EVERY subsequent turn, duplicating content that the memory archive already
surfaces on demand via hybrid recall. Older folds now live only in the archive.

Existing L1 text is discarded deliberately -- it is a lossy re-summary of folds
that are already stored verbatim as MemoryChunks, so nothing is actually lost.

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-07
"""

from alembic import op
import sqlalchemy as sa

revision = "b2c3d4e5f6a7"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("conversations", "summary2_text")


def downgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("summary2_text", sa.Text(), nullable=True),
    )
