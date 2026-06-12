"""System statistics API routes."""

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.document import Document, Chunk
from app.models.conversation import Conversation, Message
from app.services.cache import answer_cache
from app.services.auth import get_current_admin
from app.schemas.retrieval import StatsOverview, HotQuestion

router = APIRouter(prefix="/api/stats", tags=["Stats"])


@router.get("/overview", response_model=StatsOverview)
async def get_overview(current_user: User = Depends(get_current_admin), db: AsyncSession = Depends(get_db)):
    """Get system overview statistics."""

    # Document count
    doc_count_result = await db.execute(select(func.count()).select_from(Document))
    doc_count = doc_count_result.scalar() or 0

    # Chunk count
    chunk_count_result = await db.execute(select(func.count()).select_from(Chunk))
    chunk_count = chunk_count_result.scalar() or 0

    # Conversation count
    conv_count_result = await db.execute(select(func.count()).select_from(Conversation))
    conv_count = conv_count_result.scalar() or 0

    # Message count
    msg_count_result = await db.execute(select(func.count()).select_from(Message))
    msg_count = msg_count_result.scalar() or 0

    # Cache stats
    cache_stats = answer_cache.stats

    # Today's token cost (rough estimate from messages)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    msgs_today_result = await db.execute(
        select(Message).where(
            Message.created_at >= today,
            Message.role == "assistant",
        )
    )
    msgs_today = msgs_today_result.scalars().all()
    total_tokens = sum(
        len(m.content) // 3  # rough char→token estimate
        for m in msgs_today
    )
    # Estimate cost: ~$0.15/1M tokens for gpt-4o-mini input, $0.60 for output
    today_cost = (total_tokens / 1_000_000) * 0.60

    # Hot questions (top 5)
    hot_result = await db.execute(
        select(Message.content, func.count())
        .where(Message.role == "user")
        .group_by(Message.content)
        .order_by(func.count().desc())
        .limit(5)
    )
    hot_questions = [
        HotQuestion(question=row[0][:80], count=row[1])
        for row in hot_result.all()
    ]

    # Recent conversations (top 5)
    recent_result = await db.execute(
        select(Conversation).order_by(Conversation.updated_at.desc()).limit(5)
    )
    recent = recent_result.scalars().all()
    recent_convs = [
        {
            "id": c.id,
            "title": c.title,
            "kb_id": c.kb_id,
            "created_at": c.created_at.isoformat(),
            "updated_at": c.updated_at.isoformat(),
        }
        for c in recent
    ]

    return StatsOverview(
        document_count=doc_count,
        chunk_count=chunk_count,
        conversation_count=conv_count,
        message_count=msg_count,
        cache_hit_rate=cache_stats["hit_rate"],
        today_token_cost=round(today_cost, 4),
        hot_questions=hot_questions,
        recent_conversations=recent_convs,
    )
