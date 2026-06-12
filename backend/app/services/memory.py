"""Mem0 memory service — cross-session memory for RAG conversations.

All Mem0 operations run in executor threads to avoid blocking async loop.
"""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from mem0 import Memory

from app.config import settings

logger = logging.getLogger("erag")

# Dedicated thread pool for Mem0 (avoids blocking asyncio)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mem0")

_memory: Memory | None = None


def _get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory.from_config({
            "version": "v1.1",
            "vector_store": {
                "provider": "chroma",
                "config": {
                    "collection_name": "erag_memory",
                    "path": str(settings.chroma_path),
                },
            },
            "llm": {
                "provider": "openai",
                "config": {
                    "model": settings.llm_model,
                    "api_key": settings.llm_api_key,
                    "openai_base_url": settings.llm_base_url,
                    "temperature": 0.1,
                    "max_tokens": 500,
                },
            },
            "embedder": {
                "provider": "huggingface",
                "config": {"model": settings.embedding_model},
            },
            "history_db_path": str(settings.data_dir / "memory.db"),
        })
    return _memory


async def add_memory(text: str, user_id: str, metadata: dict | None = None):
    """Extract and store a memory from conversation (runs in executor)."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            _add_memory_sync, text, user_id, metadata
        )
    except Exception as e:
        logger.warning("Mem0 add error: %s", e)
        return []


def _add_memory_sync(text: str, user_id: str, metadata: dict | None = None):
    m = _get_memory()
    return m.add(text, user_id=user_id, metadata=metadata or {})


async def search_memories(query: str, user_id: str, limit: int = 5) -> list[dict]:
    """Search relevant memories (runs in executor)."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            _search_memories_sync, query, user_id, limit
        )
    except Exception as e:
        logger.warning("Mem0 search error: %s", e)
        return []


def _search_memories_sync(query: str, user_id: str, limit: int) -> list[dict]:
    m = _get_memory()
    return m.search(query, user_id=user_id, limit=limit) or []


async def get_all_memories(user_id: str) -> list[dict]:
    """Get all memories for a user."""
    try:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            _executor,
            _get_all_sync, user_id
        )
    except Exception:
        return []


def _get_all_sync(user_id: str) -> list[dict]:
    m = _get_memory()
    return m.get_all(user_id=user_id) or []


async def delete_memory(memory_id: str):
    """Delete a specific memory."""
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(_executor, _del_sync, memory_id)
    except Exception:
        pass


def _del_sync(memory_id: str):
    m = _get_memory()
    m.delete(memory_id)
