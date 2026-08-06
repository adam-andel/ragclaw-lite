"""add memory archive (L1 summary2 + memory_chunks)

Revision ID: 8b4f0a1c2d05
Revises: 7a3e9c1b2d04
Create Date: 2026-08-05 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '8b4f0a1c2d05'
down_revision: Union[str, Sequence[str], None] = '7a3e9c1b2d04'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Three-tier memory: L1 is the "secondary summary" (read-only, re-compacted
    # when it grows past the LOW% threshold). summary_archived_count is display-only.
    op.add_column(
        "conversations",
        sa.Column("summary2_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_archived_count", sa.Integer(), nullable=False, server_default="0"),
    )

    op.create_table(
        "memory_chunks",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), nullable=False),
        sa.Column("mem_kb_id", sa.String(36), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("heading", sa.String(200), nullable=False, server_default=""),
        sa.Column("page", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("embedded", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Index("ix_memory_chunks_conversation_id", "conversation_id"),
        sa.Index("ix_memory_chunks_mem_kb_id", "mem_kb_id"),
    )


def downgrade() -> None:
    op.drop_table("memory_chunks")
    op.drop_column("conversations", "summary_archived_count")
    op.drop_column("conversations", "summary2_text")
