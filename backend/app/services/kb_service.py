"""Knowledge base helpers — DB lookups shared by chat/prompt assembly.

Centralizing the kb_prompt fetch here keeps every answer-cache call site and
every system-prompt builder using the *same* KB instruction string, which is
required for cache-key consistency.
"""

from app.database import async_session
from app.models.knowledge_base import KnowledgeBase


async def get_kb_prompt(kb_id: str) -> str:
    """Return the KB's instruction prompt, or empty string if none/unavailable.

    Used to inject KB-specific guidance into the LLM system prompt and to
    salt the answer-cache key so editing the prompt busts stale answers.
    """
    if not kb_id:
        return ""
    try:
        async with async_session() as db:
            kb = await db.get(KnowledgeBase, kb_id)
            if kb is None:
                return ""
            return kb.prompt or ""
    except Exception:
        # Never let a DB hiccup block chat; just skip the KB instruction.
        return ""
