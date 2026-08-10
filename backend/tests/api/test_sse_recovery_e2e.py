"""End-to-end SSE recovery flow for durable pending-limit (Human-in-the-Loop).

Exercises the FULL chain without a live LLM by patching the agent graph:

  Phase 1  POST /api/chat/stream (new question)   → graph returns pending_limit
            chat.py saves snapshot to DB + emits `need_user_input`, ends.
  Phase 2  GET  /api/conversations/{cid}/pending   → durable pause restored
            (simulates frontend restore after page refresh / process restart).
  Phase 3  POST /api/chat/stream resume_action=continue → snapshot replayed,
            quota recharged, graph returns final answer, pending cleared.
  Phase 4  POST /api/chat/stream resume_action=stop     → pending cleared,
            message persisted with status="stopped".

The four phases are the complete "SSE end-to-end recovery flow".
"""

import json
import uuid
import pytest
from sqlalchemy import select

import app.database as _db_mod
from app.database import async_session as _async_session  # noqa: F401
from app.models.conversation import Conversation
from app.models.conversation import PendingLimitState as _PLS
from app.services.agent_graph import ragclaw_agent_graph
from app.services.config_manager import config_manager


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


async def _create_conv(user_id: str, kb_id: str, title: str = "Recovery E2E") -> str:
    cid = str(uuid.uuid4())
    async with _db_mod.async_session() as db:
        db.add(Conversation(id=cid, title=title, kb_id=kb_id, user_id=user_id))
        await db.commit()
    return cid


async def _db_pending(cid: str):
    from app.routers.chat import _load_pending_state
    async with _db_mod.async_session() as db:
        return await _load_pending_state(db, cid)


def _pending_state_payload() -> dict:
    """Graph result that triggers a durable pause (skill_switch limit)."""
    return {
        "query": "生成季度报告",
        "final_answer": "",
        "cache_hit": False,
        "citations": [],
        # snapshot fields required by _snapshot_state
        "active_skill": {"id": "s1", "name": "doc-gen"},
        "available_tools": [],
        "rag_context": "",
        "tool_results": [],
        "tool_messages": [],
        "download_entries": [],
        "skill_stack": [],
        "loaded_skill_ids": ["s1"],
        "workspace_id": "ws-1",
        "skill_switch_count": 1,
        "tool_round": 2,
        "skill_switch_quota": config_manager.skill_switch_quota,
        "tool_round_quota": config_manager.agent_round_quota,
        # the pause
        "pending_limit": {
            "kind": "skill_switch",
            "message": "已达技能切换上限，请选择「继续」或「停止」。",
            "deferred_tool_call": {"id": "call_1", "function": {"name": "use_skill"}},
        },
    }


def _answer_state_payload() -> dict:
    """Graph result that resumes successfully (continue) and produces final text."""
    return {
        "final_answer": "已追加额度并重放技能调用，生成最终答复。",
        "cache_hit": True,  # bypass real LLM streaming
        "citations": [{"doc_id": "d1", "doc_name": "手册", "score": 0.9}],
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
        "skill_switch_count": 1,
        "tool_round": 2,
        "skill_switch_quota": config_manager.skill_switch_quota,
        "tool_round_quota": config_manager.agent_round_quota,
    }


@pytest.mark.asyncio
async def test_sse_recovery_e2e(client, user_token, test_kb, monkeypatch):
    uid = _uid(user_token)
    cid = await _create_conv(uid, test_kb["id"])

    # Behavior switch: graph acts as "pending limiter" then "answer producer".
    behavior = {"mode": "pending"}

    async def fake_run(initial_state: dict) -> dict:
        return _pending_state_payload() if behavior["mode"] == "pending" else _answer_state_payload()

    monkeypatch.setattr(ragclaw_agent_graph, "run", fake_run)

    # ── Phase 1: new question → durable pause ──
    res = await client.post("/api/chat/stream", json={
        "query": "生成季度报告", "kb_id": test_kb["id"], "conversation_id": cid,
    }, headers=_auth(user_token), timeout=60)
    assert res.status_code == 200
    events1 = _parse_sse(res.text)
    need_input = [e for e in events1 if e.get("type") == "need_user_input"]
    assert need_input, f"expected need_user_input, got: {events1[:3]}"
    assert need_input[0]["kind"] == "skill_switch"
    assert need_input[0]["conv_id"] == cid

    # snapshot persisted to DB
    pending = await _db_pending(cid)
    assert pending is not None
    assert pending["pending_limit"]["kind"] == "skill_switch"
    assert pending["pending_msg_id"] == need_input[0]["message_id"]

    # ── Phase 2: refresh → GET /pending restores the pause ──
    res2 = await client.get(f"/api/conversations/{cid}/pending", headers=_auth(user_token))
    assert res2.status_code == 200
    body = res2.json()
    assert body["conversation_id"] == cid
    assert body["kind"] == "skill_switch"
    assert "技能切换" in body["message"]

    # ── Phase 3: resume_action=continue → replay + final answer ──
    behavior["mode"] = "answer"
    res3 = await client.post("/api/chat/stream", json={
        "query": "继续", "kb_id": test_kb["id"], "conversation_id": cid,
        "resume_action": "continue",
    }, headers=_auth(user_token), timeout=60)
    assert res3.status_code == 200
    events3 = _parse_sse(res3.text)
    tokens3 = [e for e in events3 if e.get("type") == "token"]
    assert tokens3, f"expected token events on continue, got: {events3[:3]}"
    done3 = [e for e in events3 if e.get("type") == "done"]
    assert done3, f"expected done event on continue, got: {events3[:3]}"
    assert done3[0].get("cache_hit") is True

    # pending cleared after continue
    assert await _db_pending(cid) is None

    # the pending placeholder message was replaced by the final answer
    async with _db_mod.async_session() as db:
        rows = (await db.execute(
            select(_PLS).where(_PLS.conversation_id == cid)
        )).scalars().all()
        assert len(rows) == 0

    # ── Phase 4: re-pause then resume_action=stop ──
    behavior["mode"] = "pending"
    res4 = await client.post("/api/chat/stream", json={
        "query": "再来一次", "kb_id": test_kb["id"], "conversation_id": cid,
    }, headers=_auth(user_token), timeout=60)
    assert res4.status_code == 200
    assert [e for e in _parse_sse(res4.text) if e.get("type") == "need_user_input"]
    assert await _db_pending(cid) is not None

    res5 = await client.post("/api/chat/stream", json={
        "query": "停止", "kb_id": test_kb["id"], "conversation_id": cid,
        "resume_action": "stop",
    }, headers=_auth(user_token), timeout=60)
    assert res5.status_code == 200
    events5 = _parse_sse(res5.text)
    done5 = [e for e in events5 if e.get("type") == "done"]
    assert done5, f"expected done event on stop, got: {events5[:3]}"
    assert done5[0].get("stopped") is True

    # pending cleared after stop
    assert await _db_pending(cid) is None
    # the assistant message persisted with status="stopped"
    from app.models.conversation import Message
    async with _db_mod.async_session() as db:
        msgs = (await db.execute(
            select(Message).where(Message.conversation_id == cid).order_by(Message.seq.asc())
        )).scalars().all()
    stopped = [m for m in msgs if m.role == "assistant" and m.status == "stopped"]
    assert stopped, "expected an assistant message with status='stopped'"

    print("\n✅ SSE 端到端恢复流程全部通过："
          "挂起 → 刷新恢复(GET /pending) → continue 重放 → stop 终止")
