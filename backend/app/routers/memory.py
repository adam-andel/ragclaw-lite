"""Memory management API routes."""

from fastapi import APIRouter, Depends
from app.models.user import User
from app.services.auth import get_current_user
from app.services.memory import get_all_memories, search_memories, delete_memory

router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.get("")
async def list_memories(current_user: User = Depends(get_current_user), q: str = ""):
    if q:
        results = await search_memories(q, user_id=current_user.id, limit=20)
        return [{"id": r.get("id", ""), "memory": r.get("memory", ""),
                 "score": r.get("score", 0)} for r in results]
    memories = await get_all_memories(current_user.id)
    return memories


@router.delete("/{memory_id}")
async def remove_memory(memory_id: str, current_user: User = Depends(get_current_user)):
    await delete_memory(memory_id)
    return {"status": "deleted"}
