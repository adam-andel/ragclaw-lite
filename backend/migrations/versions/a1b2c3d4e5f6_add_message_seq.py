"""add per-conversation Message.seq and the seq-based summary cursor

``Message.id`` is a UUID and carries no ordering semantics, so every read path
ordered by ``created_at`` -- a ``datetime.utcnow()`` value with no tiebreaker.
Two messages inserted inside the same transaction can land on the same
timestamp, and SQLite does not guarantee a stable order for equal sort keys.

``seq`` is a per-conversation monotonic integer assigned at insert time. It
gives history a total order that survives row deletion (``_cleanup_orphan_messages``
really deletes rows, which shifts every positional index) and lets the
compression cursor be expressed as ``WHERE seq > cursor`` instead of a fragile
list offset.

Backfill uses ``ROW_NUMBER() OVER (PARTITION BY conversation_id ORDER BY
created_at, rowid)``. ``rowid`` is SQLite's insertion order -- exactly the
tiebreaker ``created_at`` lacks.

``conversations.summary_msg_seq`` is the new cursor. It is added alongside the
legacy positional ``summary_msg_count`` (both are kept during the migration
window) and backfilled by translating the old message index into the seq of the
last already-summarized message.

Revision ID: a1b2c3d4e5f6
Revises: 9c1d2e3f4a5b
Create Date: 2026-08-07 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, Sequence[str], None] = '9c1d2e3f4a5b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("messages", sa.Column("seq", sa.Integer(), nullable=True))
    op.add_column(
        "conversations",
        sa.Column("summary_msg_seq", sa.Integer(), nullable=False, server_default="0"),
    )

    # Backfill seq: 1..n per conversation, ordered by (created_at, rowid).
    bind.execute(sa.text("""
        WITH ordered AS (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY conversation_id
                       ORDER BY created_at, rowid
                   ) AS rn
              FROM messages
        )
        UPDATE messages
           SET seq = (SELECT rn FROM ordered WHERE ordered.id = messages.id)
    """))

    # Translate the legacy positional cursor into a seq cursor: summary_msg_count
    # counted how many of the earliest messages were folded, so the new cursor is
    # the seq of the summary_msg_count-th message (0 when nothing was folded).
    bind.execute(sa.text("""
        UPDATE conversations
           SET summary_msg_seq = COALESCE((
                   SELECT MAX(m.seq)
                     FROM messages m
                    WHERE m.conversation_id = conversations.id
                      AND m.seq <= conversations.summary_msg_count
               ), 0)
         WHERE COALESCE(summary_msg_count, 0) > 0
    """))

    op.create_index(
        "ix_messages_conv_seq", "messages", ["conversation_id", "seq"], unique=True
    )


def downgrade() -> None:
    op.drop_index("ix_messages_conv_seq", table_name="messages")
    op.drop_column("conversations", "summary_msg_seq")
    op.drop_column("messages", "seq")
