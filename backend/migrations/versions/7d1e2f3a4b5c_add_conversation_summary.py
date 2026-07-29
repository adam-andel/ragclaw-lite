"""add conversation summary columns

Revision ID: 7d1e2f3a4b5c
Revises: 6c3d4e5f6071
Create Date: 2026-07-28 19:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7d1e2f3a4b5c'
down_revision: Union[str, Sequence[str], None] = '6c3d4e5f6071'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Accumulated compressed transcript of the oldest conversation turns, plus a
    # cursor tracking how many earliest messages are already summarized. Raw
    # messages are never touched — this only affects what is injected into the
    # LLM context.
    op.add_column(
        "conversations",
        sa.Column("summary_text", sa.Text(), nullable=True),
    )
    op.add_column(
        "conversations",
        sa.Column("summary_msg_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("conversations", "summary_msg_count")
    op.drop_column("conversations", "summary_text")
