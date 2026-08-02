"""add user memory field

Revision ID: 7a3e9c1b2d04
Revises: 7d1e2f3a4b5c
Create Date: 2026-08-02 22:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '7a3e9c1b2d04'
down_revision: Union[str, None] = '7d1e2f3a4b5c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # User-authored free-text memory & preferences (manual, NOT MEM0-extracted).
    # Injected into the LLM system prompt as part of the task background.
    op.add_column('users', sa.Column('memory', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('users', 'memory')
