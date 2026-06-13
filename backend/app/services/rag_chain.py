"""RAG query chain: memory -> retrieval -> prompt -> LLM generation.

Memory search is disabled during chat (adds ~2-3s LLM call latency).
Memory storage runs after chat completes (fire-and-forget).
"""

import asyncio
import logging
import time
from typing import AsyncGenerator

from app.services.hybrid_search import hybrid_search
from app.services.llm_client import llm_client
from app.services.cache import answer_cache
from app.services.memory import add_memory

logger = logging.getLogger("erag")

RAG_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""


def _build_context(retrieved: list[dict]) -> tuple[str, list[dict]]:
    parts, citations = [], []
    for i, r in enumerate(retrieved):
        doc_name = r.get("doc_id", "?")[:8]
        heading = r.get("heading", "") or ""
        parts.append(f"[{i+1}] {doc_name} {heading}\n{r['content']}")
        citations.append({
            "doc_id": r.get("doc_id", ""), "doc_name": doc_name,
            "heading": heading, "page": r.get("page"),
            "content_snippet": r["content"][:200], "score": round(r["fusion_score"], 4),
        })
    return "\n\n---\n\n".join(parts), citations


class RAGChain:

    async def query_stream(
        self, question: str, kb_id: str, user_id: str = "",
        conversation_history: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        cached = answer_cache.get(question, kb_id)
        if cached:
            yield {"type": "token", "content": cached.answer}
            for c in cached.citations:
                yield {"type": "citation", "citation": c}
            yield {"type": "done", "cache_hit": True}
            return

        t_total = time.time()

        # Retrieval (fast, local)
        t_retr_start = time.time()
        retrieved = hybrid_search.search(kb_id, question)
        retrieval_ms = round((time.time() - t_retr_start) * 1000)
        print(f"[RAG] kb={kb_id[:8]} q={question[:30]} results={len(retrieved)}", flush=True)
        context_text, citations = _build_context(retrieved)

        # Build prompt
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": f"## 参考文档\n{context_text}\n\n## 问题\n{question}"})

        # LLM streaming
        full_answer = ""
        ttft_ms = 0.0
        t_start = time.time()
        first_token = True
        try:
            async for token in llm_client.chat_stream(messages):
                if first_token:
                    ttft_ms = (time.time() - t_start) * 1000
                    first_token = False
                full_answer += token
                yield {"type": "token", "content": token}
            for c in citations:
                yield {"type": "citation", "citation": c}

            # Store memory in background (fire-and-forget)
            if user_id and full_answer:
                asyncio.create_task(
                    _store_memory(f"Q: {question}\nA: {full_answer[:500]}", user_id)
                )

            answer_cache.put(question, kb_id, full_answer, citations)
            total_ttft = round((time.time() - t_total) * 1000)
            yield {"type": "done", "cache_hit": False, "ttft_ms": total_ttft, "retrieval_ms": retrieval_ms, "llm_ms": round(ttft_ms)}
        except Exception as e:
            logger.error("RAG stream error: %s", e)
            yield {"type": "error", "message": str(e)}


async def _store_memory(text: str, user_id: str):
    """Store memory in background, never blocks chat."""
    try:
        await add_memory(text, user_id=user_id)
        print(f"[Mem0] stored memory for user={user_id[:8]}", flush=True)
    except Exception as e:
        logger.warning("Mem0 store error: %s", e)


rag_chain = RAGChain()
