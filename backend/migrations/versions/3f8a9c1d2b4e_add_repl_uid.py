"""add repl_uid to users

Revision ID: 3f8a9c1d2b4e
Revises: 2624081b4b65
Create Date: 2026-07-16 22:50:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3f8a9c1d2b4e'
down_revision: Union[str, None] = '2624081b4b65'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Per-user dedicated Linux UID for the REPL sandbox isolation account.
    # NULL allowed so existing (legacy) users keep using the sandbox's
    # deterministic hash fallback until they are migrated. The unique index
    # ignores NULLs (multiple NULLs permitted), so it only constrains the
    # randomly-assigned UIDs of new users.
    op.add_column('users', sa.Column('repl_uid', sa.Integer(), nullable=True))
    op.create_index('ix_users_repl_uid', 'users', ['repl_uid'], unique=True)


def downgrade() -> None:
    op.drop_index('ix_users_repl_uid', table_name='users')
    op.drop_column('users', 'repl_uid')
