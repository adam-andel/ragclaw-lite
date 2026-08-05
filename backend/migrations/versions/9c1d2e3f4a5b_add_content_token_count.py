"""add content_token_count to messages

Stores each message's own content tokens (+4 per-message overhead) written at
insert time, so the Layer-1 history compression can locate the oldest 2/3 token
boundary by whole conversation rounds without re-encoding the full history every
turn. Distinct from `token_count` (whole-turn prompt tokens, surfaced in the UI
capacity bar).

Revision ID: 9c1d2e3f4a5b
Revises: 8b4f0a1c2d05
Create Date: 2026-08-05 19:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9c1d2e3f4a5b'
down_revision: Union[str, Sequence[str], None] = '8b4f0a1c2d05'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "messages",
        sa.Column("content_token_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("messages", "content_token_count")
