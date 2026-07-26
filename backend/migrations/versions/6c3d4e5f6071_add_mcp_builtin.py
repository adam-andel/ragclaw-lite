"""add is_builtin to mcp_servers

Revision ID: 6c3d4e5f6071
Revises: 5b2c3d4e5f60
Create Date: 2026-07-26 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '6c3d4e5f6071'
down_revision: Union[str, Sequence[str], None] = '5b2c3d4e5f60'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Flag platform-mandated MCP servers (e.g. Python Executor) so they can be
    # hidden from the user-managed MCP list and protected from delete/rename.
    # NOT NULL + default 0 keeps the ALTER valid for tables with existing rows.
    op.add_column(
        "mcp_servers",
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("mcp_servers", "is_builtin")
