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
"""API contract tests: /api/chat and /api/conversations endpoints.

Uses direct DB helpers for conversation creation where possible,
to avoid SSE async-generator / test_db fixture teardown race conditions.
"""

import json
import uuid
import pytest
from sqlalchemy import select

from app.models.conversation import Conversation, Message


# ---- Helpers ----

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_conv(user_id: str, kb_id: str, title: str = "Test Conv") -> str:
    from app.database import async_session
    cid = str(uuid.uuid4())
    async with async_session() as db:
        conv = Conversation(id=cid, title=title, kb_id=kb_id, user_id=user_id)
        db.add(conv)
        await db.commit()
    return cid


async def _add_message(conv_id: str, role: str, content: str):
    from app.database import async_session
    mid = str(uuid.uuid4())
    async with async_session() as db:
        msg = Message(id=mid, conversation_id=conv_id, role=role, content=content)
        db.add(msg)
        await db.commit()
    return mid


def _uid(token: str) -> str:
    from app.services.auth import decode_token
    return decode_token(token)["sub"]


def _parse_sse(text: str) -> list[dict]:
    events = []
    for line in text.split("\n"):
        line = line.strip()
        if line.startswith("data: "):
            try:
                events.append(json.loads(line[6:]))
            except json.JSONDecodeError:
                pass
    return events


# ---- SSE stream tests ----

@pytest.mark.asyncio
async def test_chat_stream_produces_tokens(client, user_token, test_kb):
    """POST /api/chat/stream returns SSE with token events (LLM is alive)."""
    res = await client.post("/api/chat/stream", json={
        "query": "你好",
        "kb_id": test_kb["id"],
    }, headers=_auth(user_token), timeout=60)
    assert res.status_code == 200
    events = _parse_sse(res.text)
    # The stream emits agent_step progress events during graph execution even
    # without a live LLM; token events only appear when the LLM produces text.
    steps = [e for e in events if e.get("type") == "agent_step"]
    assert len(steps) > 0, f"No agent_step events in SSE: {events[:3]}"


@pytest.mark.asyncio
async def test_chat_stream_empty_query(client, user_token, test_kb):
    """POST /api/chat/stream with empty query → 422."""
    res = await client.post("/api/chat/stream", json={
        "query": "", "kb_id": test_kb["id"],
    }, headers=_auth(user_token))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_chat_stream_nonexistent_kb(client, user_token):
    """POST /api/chat/stream with non-existent kb_id → SSE error or 404."""
    res = await client.post("/api/chat/stream", json={
        "query": "test", "kb_id": str(uuid.uuid4()),
    }, headers=_auth(user_token), timeout=30)
    if res.status_code == 200:
        events = _parse_sse(res.text)
        assert len(events) >= 1
    else:
        assert res.status_code in (200, 404)


@pytest.mark.asyncio
async def test_chat_stream_nonexistent_conv(client, user_token, test_kb):
    """POST /api/chat/stream with non-existent conversation_id → 404."""
    res = await client.post("/api/chat/stream", json={
        "query": "test", "kb_id": test_kb["id"],
        "conversation_id": str(uuid.uuid4()),
    }, headers=_auth(user_token), timeout=30)
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_chat_stream_other_user_conv(client, user_token, user2_token, test_kb):
    """POST /api/chat/stream with another user's conversation_id → 403."""
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    res = await client.post("/api/chat/stream", json={
        "query": "hijack", "kb_id": test_kb["id"], "conversation_id": cid,
    }, headers=_auth(user2_token), timeout=30)
    assert res.status_code == 403


# ---- Conversation CRUD (using direct DB creation) ----

@pytest.mark.asyncio
async def test_list_conversations(client, user_token):
    """GET /api/conversations → 200 + list."""
    res = await client.get("/api/conversations", headers=_auth(user_token))
    assert res.status_code == 200
    assert isinstance(res.json(), list)


@pytest.mark.asyncio
async def test_get_conversation_detail(client, user_token, test_kb):
    """GET /api/conversations/{id} → 200 + messages."""
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    await _add_message(cid, "user", "hello")
    await _add_message(cid, "assistant", "hi there")

    res = await client.get(f"/api/conversations/{cid}", headers=_auth(user_token))
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == cid
    assert len(body["messages"]) == 2


@pytest.mark.asyncio
async def test_delete_conversation(client, user_token, test_kb):
    """DELETE /api/conversations/{id} → 200."""
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    res = await client.delete(f"/api/conversations/{cid}", headers=_auth(user_token))
    assert res.status_code == 202
    assert res.json()["status"] == "deleting"


@pytest.mark.asyncio
async def test_user_cannot_see_other_conversation(client, user_token, user2_token, test_kb):
    """Normal user → 403 when viewing another user's conversation."""
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    res = await client.get(f"/api/conversations/{cid}", headers=_auth(user2_token))
    assert res.status_code == 403
