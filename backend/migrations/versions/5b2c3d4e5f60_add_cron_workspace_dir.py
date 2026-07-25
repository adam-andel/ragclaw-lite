"""add workspace_dir to cron_jobs

Revision ID: 5b2c3d4e5f60
Revises: 4a1b2c3d5e6f
Create Date: 2026-07-25 16:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '5b2c3d4e5f60'
down_revision: Union[str, Sequence[str], None] = '4a1b2c3d5e6f'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Persist the working directory selected in the chat when the cron job was
    # created, so execution can restore the same scene (kb / skill / workspace).
    op.add_column(
        "cron_jobs",
        sa.Column("workspace_dir", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("cron_jobs", "workspace_dir")
