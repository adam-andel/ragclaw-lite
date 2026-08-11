"""Smoke tests for durable pending-limit snapshot (DB persistence + recovery).

These tests do NOT require a live LLM — they exercise the snapshot
save/load/clear helpers and the GET /pending endpoint directly.
"""

import json
import uuid
import pytest
from sqlalchemy import select

from app.database import async_session as _async_session  # noqa: F401 (kept for imports)
import app.database as _db
from app.models.conversation import Conversation, Message
from app.routers.chat import (
    _save_pending_state,
    _load_pending_state,
    _clear_pending_state,
)


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _create_conv(user_id: str, kb_id: str, title: str = "Pending Test") -> str:
    cid = str(uuid.uuid4())
    # Read the sessionmaker via module attribute so the test_db override applies.
    async with _db.async_session() as db:
        db.add(Conversation(id=cid, title=title, kb_id=kb_id, user_id=user_id))
        await db.commit()
    return cid


def _uid(token: str) -> str:
    from app.services.auth import decode_token
    return decode_token(token)["sub"]


def _sample_snapshot() -> dict:
    return {
        "query": "生成一份季度报告",
        "active_skill": {"id": "s1", "name": "doc-gen", "system_prompt": "..."},
        "available_tools": [{"function": {"name": "run_python"}}],
        "rag_context": "",
        "citations": [],
        "tool_results": [],
        "tool_messages": [],
        "download_entries": [],
        "skill_stack": [],
        "loaded_skill_ids": ["s1"],
        "subdir": "ws-1",
        "skill_switch_count": 1,
        "tool_round": 2,
        "skill_switch_quota": 3,
        "tool_round_quota": 2,
        "pending_limit": {
            "kind": "skill_switch",
            "message": "技能切换次数已用尽，请选择「继续」或「停止」。",
            "deferred_tool_call": {"id": "call_1", "function": {"name": "use_skill"}},
        },
    }


# ---- Persistence helper round-trip ----

@pytest.mark.asyncio
async def test_pending_state_roundtrip(test_db):
    cid = str(uuid.uuid4())
    snap = _sample_snapshot()
    msg_id = str(uuid.uuid4())

    async with _db.async_session() as db:
        await _save_pending_state(db, cid, msg_id, snap)
        loaded = await _load_pending_state(db, cid)
    assert loaded is not None
    assert loaded["pending_msg_id"] == msg_id
    assert loaded["pending_limit"]["kind"] == "skill_switch"
    # deferred_tool_call survives JSON round-trip
    assert loaded["pending_limit"]["deferred_tool_call"]["function"]["name"] == "use_skill"

    msg_id2 = str(uuid.uuid4())
    async with _db.async_session() as db:
        await _save_pending_state(db, cid, msg_id2, snap)
        loaded2 = await _load_pending_state(db, cid)
        # clear
        await _clear_pending_state(db, cid)
        loaded3 = await _load_pending_state(db, cid)
    assert loaded2["pending_msg_id"] == msg_id2
    assert loaded3 is None


# ---- GET /pending endpoint ----

@pytest.mark.asyncio
async def test_pending_none_when_absent(client, user_token, test_kb):
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    res = await client.get(f"/api/conversations/{cid}/pending", headers=_auth(user_token))
    assert res.status_code == 200
    assert res.json() is None


@pytest.mark.asyncio
async def test_pending_returned_when_set(client, user_token, test_kb):
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    snap = _sample_snapshot()
    msg_id = str(uuid.uuid4())
    async with _db.async_session() as db:
        await _save_pending_state(db, cid, msg_id, snap)

    res = await client.get(f"/api/conversations/{cid}/pending", headers=_auth(user_token))
    assert res.status_code == 200
    body = res.json()
    assert body["conversation_id"] == cid
    assert body["message_id"] == msg_id
    assert body["kind"] == "skill_switch"
    assert "技能切换" in body["message"]


@pytest.mark.asyncio
async def test_pending_404_missing_conv(client, user_token):
    res = await client.get(f"/api/conversations/{uuid.uuid4()}/pending", headers=_auth(user_token))
    assert res.status_code == 404


@pytest.mark.asyncio
async def test_pending_403_other_user(client, user_token, user2_token, test_kb):
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    async with _db.async_session() as db:
        await _save_pending_state(db, cid, str(uuid.uuid4()), _sample_snapshot())
    res = await client.get(f"/api/conversations/{cid}/pending", headers=_auth(user2_token))
    assert res.status_code == 403


@pytest.mark.asyncio
async def test_pending_cleared_on_delete(client, user_token, test_kb):
    cid = await _create_conv(_uid(user_token), test_kb["id"])
    async with _db.async_session() as db:
        await _save_pending_state(db, cid, str(uuid.uuid4()), _sample_snapshot())
    res = await client.delete(f"/api/conversations/{cid}", headers=_auth(user_token))
    assert res.status_code == 200
    res2 = await client.get(f"/api/conversations/{cid}/pending", headers=_auth(user_token))
    assert res2.status_code == 404  # conversation gone
