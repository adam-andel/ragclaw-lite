"""RAG query chain: retrieval -> prompt building -> LLM generation."""

import logging
from typing import AsyncGenerator

from app.services.hybrid_search import hybrid_search
from app.services.llm_client import llm_client
from app.services.cache import answer_cache

logger = logging.getLogger("erag")

RAG_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""


class RAGChain:
    """Orchestrates the full RAG pipeline: retrieve -> prompt -> generate."""

    async def query(
        self, question: str, kb_id: str,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        cached = answer_cache.get(question, kb_id)
        if cached:
            return {"answer": cached.answer, "citations": cached.citations, "cache_hit": True}

        retrieved = hybrid_search.search(kb_id, question)
        logger.info("RAG query kb=%s q=%s results=%d", kb_id[:8], question[:30], len(retrieved))

        context_parts = []
        citations = []
        for i, r in enumerate(retrieved):
            doc_name = r.get("doc_id", "?")[:8]
            heading = r.get("heading", "") or ""
            context_parts.append(f"[{i+1}] {doc_name} {heading}\n{r['content']}")
            citations.append({
                "doc_id": r.get("doc_id", ""), "doc_name": doc_name,
                "heading": heading, "page": r.get("page"),
                "content_snippet": r["content"][:200], "score": round(r["fusion_score"], 4),
            })

        context = "\n\n---\n\n".join(context_parts)
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": f"## 参考文档\n{context}\n\n## 问题\n{question}"})

        answer = await llm_client.chat(messages)
        answer_cache.put(question, kb_id, answer, citations)
        return {"answer": answer, "citations": citations, "cache_hit": False}

    async def query_stream(
        self, question: str, kb_id: str,
        conversation_history: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        cached = answer_cache.get(question, kb_id)
        if cached:
            yield {"type": "token", "content": cached.answer}
            for c in cached.citations:
                yield {"type": "citation", "citation": c}
            yield {"type": "done", "cache_hit": True}
            return

        retrieved = hybrid_search.search(kb_id, question)
        print(f"[RAG] kb={kb_id[:8]} q={question[:30]} results={len(retrieved)}", flush=True)

        context_parts = []
        citations = []
        for i, r in enumerate(retrieved):
            doc_name = r.get("doc_id", "?")[:8]
            heading = r.get("heading", "") or ""
            context_parts.append(f"[{i+1}] {doc_name} {heading}\n{r['content']}")
            citations.append({
                "doc_id": r.get("doc_id", ""), "doc_name": doc_name,
                "heading": heading, "page": r.get("page"),
                "content_snippet": r["content"][:200], "score": round(r["fusion_score"], 4),
            })

        context = "\n\n---\n\n".join(context_parts)
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": f"## 参考文档\n{context}\n\n## 问题\n{question}"})

        full_answer = ""
        try:
            async for token in llm_client.chat_stream(messages):
                full_answer += token
                yield {"type": "token", "content": token}
            for c in citations:
                yield {"type": "citation", "citation": c}
            answer_cache.put(question, kb_id, full_answer, citations)
            yield {"type": "done", "cache_hit": False}
        except Exception as e:
            logger.error("RAG stream error: %s", e)
            yield {"type": "error", "message": str(e)}


rag_chain = RAGChain()
