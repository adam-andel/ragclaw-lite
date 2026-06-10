"""RAG query chain: retrieval → prompt building → LLM generation."""

from typing import AsyncGenerator

from app.services.hybrid_search import hybrid_search
from app.services.llm_client import llm_client
from app.services.cache import answer_cache

RAG_SYSTEM_PROMPT = """你是一个企业知识库助手。根据提供的文档内容回答问题。

## 规则
1. 只根据提供的文档内容回答，不要编造信息
2. 如果文档中没有相关信息，诚实地说"文档中未找到相关信息"
3. 在回答中标注引用来源，格式：[来源: 文档名 章节名]
4. 回答要简洁、准确，使用中文
5. 如果文档内容包含代码或表格，保留原始格式"""


class RAGChain:
    """Orchestrates the full RAG pipeline: retrieve → prompt → generate."""

    async def query(
        self,
        question: str,
        kb_id: str,
        conversation_history: list[dict] | None = None,
    ) -> dict:
        """Non-streaming RAG query. Returns dict with answer and citations."""
        # Try cache first
        cached = answer_cache.get(question, kb_id)
        if cached:
            return {
                "answer": cached.answer,
                "citations": cached.citations,
                "cache_hit": True,
            }

        # Retrieve
        retrieved = hybrid_search.search(kb_id, question)

        # Build context
        context_parts = []
        citations = []
        for i, r in enumerate(retrieved):
            doc_name = r.get("doc_id", "未知")[:8]
            heading = r.get("heading", "") or ""
            context_parts.append(
                f"[文档片段 {i+1}] 来源: {doc_name} {heading}\n{r['content']}"
            )
            citations.append({
                "doc_id": r.get("doc_id", ""),
                "doc_name": doc_name,
                "heading": heading,
                "page": r.get("page"),
                "content_snippet": r["content"][:200],
                "score": round(r["fusion_score"], 4),
            })

        context = "\n\n---\n\n".join(context_parts)

        # Build messages
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]

        if conversation_history:
            messages.extend(conversation_history)

        user_message = f"""## 参考文档内容

{context}

## 用户问题

{question}"""

        messages.append({"role": "user", "content": user_message})

        # Generate
        answer = await llm_client.chat(messages)

        # Cache result
        answer_cache.put(question, kb_id, answer, citations)

        return {
            "answer": answer,
            "citations": citations,
            "cache_hit": False,
        }

    async def query_stream(
        self,
        question: str,
        kb_id: str,
        conversation_history: list[dict] | None = None,
    ) -> AsyncGenerator[dict, None]:
        """Streaming RAG query. Yields dict events: token, citation, done, error."""
        # Try cache first
        cached = answer_cache.get(question, kb_id)
        if cached:
            yield {"type": "token", "content": cached.answer}
            for c in cached.citations:
                yield {"type": "citation", "citation": c}
            yield {"type": "done", "cache_hit": True}
            return

        # Retrieve
        retrieved = hybrid_search.search(kb_id, question)

        # Build context & citations
        context_parts = []
        citations = []
        for i, r in enumerate(retrieved):
            doc_name = r.get("doc_id", "未知")[:8]
            heading = r.get("heading", "") or ""
            context_parts.append(
                f"[文档片段 {i+1}] 来源: {doc_name} {heading}\n{r['content']}"
            )
            citations.append({
                "doc_id": r.get("doc_id", ""),
                "doc_name": doc_name,
                "heading": heading,
                "page": r.get("page"),
                "content_snippet": r["content"][:200],
                "score": round(r["fusion_score"], 4),
            })

        context = "\n\n---\n\n".join(context_parts)

        # Build messages
        messages = [{"role": "system", "content": RAG_SYSTEM_PROMPT}]

        if conversation_history:
            messages.extend(conversation_history)

        user_message = f"""## 参考文档内容

{context}

## 用户问题

{question}"""

        messages.append({"role": "user", "content": user_message})

        # Stream generation
        full_answer = ""
        try:
            async for token in llm_client.chat_stream(messages):
                full_answer += token
                yield {"type": "token", "content": token}

            # Send citations after the full answer
            for c in citations:
                yield {"type": "citation", "citation": c}

            # Cache the result
            answer_cache.put(question, kb_id, full_answer, citations)

            yield {"type": "done", "cache_hit": False}

        except Exception as e:
            yield {"type": "error", "message": str(e)}


# Singleton
rag_chain = RAGChain()
