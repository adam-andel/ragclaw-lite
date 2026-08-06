"""Reproduce double tool-round-limit: confirm backend emits need_user_input twice."""

import json
import uuid
import pytest

import app.database as _db_mod
from app.database import async_session as _async_session  # noqa: F401
from app.services.agent_graph import ragclaw_agent_graph
from app.services.config_manager import config_manager
from app.services.agent_nodes import MAX_TOOL_ROUNDS


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


async def _create_conv(user_id: str, kb_id: str) -> str:
    cid = str(uuid.uuid4())
    async with _db_mod.async_session() as db:
        from app.models.conversation import Conversation
        db.add(Conversation(id=cid, title="DoubleLimit", kb_id=kb_id, user_id=user_id))
        await db.commit()
    return cid


def _pending_state() -> dict:
    return {
        "query": "loop",
        "final_answer": "",
        "cache_hit": False,
        "citations": [],
        "active_skill": {"id": "s1", "name": "doc-gen"},
        "available_tools": [],
        "rag_context": "",
        "tool_results": [],
        "tool_messages": [],
        "download_entries": [],
        "skill_stack": [],
        "loaded_skill_ids": ["s1"],
        "workspace_id": "ws-1",
        "skill_switch_count": 0,
        "tool_round": MAX_TOOL_ROUNDS,
        "skill_switch_quota": 1,
        "tool_round_quota": MAX_TOOL_ROUNDS,
        "pending_limit": {
            "kind": "tool_round",
            "message": "Tool-call round limit reached",
            "deferred_tool_call": None,
        },
    }


def _answer_state() -> dict:
    return {
        "final_answer": "done",
        "cache_hit": True,
        "citations": [],
        "pending_limit": None,
        "active_skill": {"id": "s1", "name": "doc-gen"},
        "available_tools": [],
        "rag_context": "",
        "tool_results": [],
        "tool_messages": [],
        "download_entries": [],
        "skill_stack": [],
        "loaded_skill_ids": ["s1"],
        "workspace_id": "ws-1",
        "skill_switch_count": 0,
        "tool_round": 0,
        "skill_switch_quota": 1,
        "tool_round_quota": MAX_TOOL_ROUNDS,
    }


@pytest.fixture(autouse=True)
def _jwt_secret():
    config_manager._config["jwt_secret"] = "test_secret_0123456789abcdef0123456789abcdef"
    yield
    config_manager._config["jwt_secret"] = ""


@pytest.mark.asyncio
async def test_double_limit_emits_twice(client, user_token, test_kb, monkeypatch):
    uid = _uid(user_token)
    cid = await _create_conv(uid, test_kb["id"])

    # Behavior: pending -> (continue) -> pending again -> (continue) -> answer
    state = {"mode": "pending"}

    async def fake_run(initial_state: dict) -> dict:
        if state["mode"] == "pending":
            return _pending_state()
        return _answer_state()

    monkeypatch.setattr(ragclaw_agent_graph, "run", fake_run)

    # Phase 1: first limit
    res1 = await client.post("/api/chat/stream", json={
        "query": "loop", "kb_id": test_kb["id"], "conversation_id": cid,
    }, headers=_auth(user_token), timeout=60)
    ev1 = _parse_sse(res1.text)
    nui1 = [e for e in ev1 if e.get("type") == "need_user_input"]
    print("PHASE1 need_user_input count:", len(nui1), "ids:", [e.get("message_id") for e in nui1])
    assert nui1, "phase1 should emit need_user_input"

    # Phase 2: continue -> second limit
    state["mode"] = "pending"
    res2 = await client.post("/api/chat/stream", json={
        "query": "continue", "kb_id": test_kb["id"], "conversation_id": cid,
        "resume_action": "continue",
    }, headers=_auth(user_token), timeout=60)
    ev2 = _parse_sse(res2.text)
    nui2 = [e for e in ev2 if e.get("type") == "need_user_input"]
    print("PHASE2 need_user_input count:", len(nui2), "ids:", [e.get("message_id") for e in nui2])
    assert nui2, "phase2 (continue) should emit need_user_input again"

    # Phase 3: continue -> answer
    state["mode"] = "answer"
    res3 = await client.post("/api/chat/stream", json={
        "query": "continue", "kb_id": test_kb["id"], "conversation_id": cid,
        "resume_action": "continue",
    }, headers=_auth(user_token), timeout=60)
    ev3 = _parse_sse(res3.text)
    done3 = [e for e in ev3 if e.get("type") == "done"]
    print("PHASE3 done count:", len(done3))
    assert done3, "phase3 should emit done"

    print("\n✅ backend emits need_user_input on BOTH limits")
