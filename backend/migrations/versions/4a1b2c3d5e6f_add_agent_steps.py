"""add agent_steps table

Revision ID: 4a1b2c3d5e6f
Revises: 3f8a9c1d2b4e
Create Date: 2026-07-25 11:52:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '4a1b2c3d5e6f'
down_revision: Union[str, Sequence[str], None] = '3f8a9c1d2b4e'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Persisted agent processing trace (Route D observability). Kept fully
    # separate from the LLM context and MEM0 memory — never injected into
    # conversation_history / tool_results, never fed to the memory LLM.
    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("conversation_id", sa.String(36), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("message_id", sa.String(36), sa.ForeignKey("messages.id"), nullable=True),
        sa.Column("seq", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("stage", sa.String(50), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("extra_json", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_agent_steps_conversation_id", "agent_steps", ["conversation_id"])
    op.create_index("ix_agent_steps_message_id", "agent_steps", ["message_id"])


def downgrade() -> None:
    op.drop_index("ix_agent_steps_message_id", table_name="agent_steps")
    op.drop_index("ix_agent_steps_conversation_id", table_name="agent_steps")
    op.drop_table("agent_steps")
