# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Knowledge base helpers — DB lookups shared by chat/prompt assembly.

Centralizing the kb_prompt fetch here keeps every answer-cache call site and
every system-prompt builder using the *same* KB instruction string, which is
required for cache-key consistency.
"""

from app.database import async_session
from app.models.knowledge_base import KnowledgeBase
from app.services.config_manager import config_manager
from app.services.i18n import t


async def get_kb_prompt(kb_id: str, lang: str | None = None) -> str:
    """Return the KB's instruction prompt, or empty string if none/unavailable.

    Used to inject KB-specific guidance into the LLM system prompt and to
    salt the answer-cache key so editing the prompt busts stale answers.

    When a KB is selected, the hybrid_search meta-tool guidance is appended so
    the LLM knows when/how to retrieve on demand (resolve references from
    conversation history, rewrite into a self-contained query, etc.). The
    guidance language follows ``lang`` (default: the global prompt_language).
    """
    if not kb_id:
        return ""
    try:
        async with async_session() as db:
            kb = await db.get(KnowledgeBase, kb_id)
            if kb is None:
                return ""
            base = kb.prompt or ""
    except Exception:
        # Never let a DB hiccup block chat; just skip the KB instruction.
        return ""
    guidance = t("kb_hybrid_search_guidance", lang or config_manager.prompt_language)
    if not guidance:
        return base
    return (base + "\n\n" + guidance).strip() if base else guidance
