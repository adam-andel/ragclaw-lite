"""API tests for the LLM context-window feature.

Covers:
- PUT /api/config/llm persists llm_context_window (and it surfaces in GET + /api/health)
- PUT /api/config/llm rejects out-of-range llm_context_window (422)
- assistant messages persist the computed prompt token count (token_count column)
"""

import uuid
import pytest
from sqlalchemy import select

from app.services.config_manager import config_manager
from app.models.conversation import Conversation, Message


# ---- helpers ----

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Config API: context window
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_context_window_persists(client, admin_token):
    """Admin can set llm_context_window and it is reflected everywhere."""
    saved_config = dict(config_manager._config)
    try:
        res = await client.put("/api/config/llm", json={"llm_context_window": 100000},
                               headers=_auth(admin_token))
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["config"]["llm_context_window"] == 100000
        # reflected in the live singleton
        assert config_manager.context_window == 100000
        # reflected in health endpoint
        h = await client.get("/api/health", headers=_auth(admin_token))
        assert h.json()["context_window"] == 100000
    finally:
        config_manager._config = saved_config


@pytest.mark.asyncio
async def test_update_context_window_out_of_range_low(client, admin_token):
    res = await client.put("/api/config/llm", json={"llm_context_window": 0},
                           headers=_auth(admin_token))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_update_context_window_out_of_range_high(client, admin_token):
    res = await client.put("/api/config/llm", json={"llm_context_window": 10_000_001},
                           headers=_auth(admin_token))
    assert res.status_code == 422


@pytest.mark.asyncio
async def test_get_config_includes_context_window(client, admin_token):
    """GET returns llm_context_window once defaults are seeded (as at startup)."""
    saved_config = dict(config_manager._config)
    try:
        # In production init() seeds defaults; replicate that for the test env.
        config_manager._config = config_manager._build_non_sensitive_defaults()
        res = await client.get("/api/config/llm", headers=_auth(admin_token))
        assert res.status_code == 200
        assert "llm_context_window" in res.json()
        assert res.json()["llm_context_window"] == config_manager.context_window
    finally:
        config_manager._config = saved_config


# ---------------------------------------------------------------------------
# Persistence: assistant message token_count
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_save_assistant_message_persists_prompt_tokens(test_db):
    """_save_assistant_message writes prompt_tokens into Message.token_count."""
    from app.routers.chat import _save_assistant_message
    from app.database import async_session

    conv_id = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Conversation(id=conv_id, title="ctx test", kb_id=None, user_id=None))
        await db.commit()

    msg = await _save_assistant_message(
        conv_id, "The answer is 42.", citations=[], cache_hit=False, prompt_tokens=1234
    )
    assert msg.token_count == 1234

    # Read back from DB to confirm durability.
    async with async_session() as db:
        row = (await db.execute(
            select(Message).where(Message.id == msg.id)
        )).scalar_one()
        assert row.token_count == 1234


@pytest.mark.asyncio
async def test_save_assistant_message_updates_existing_token_count(test_db):
    """Updating an existing message in place refreshes token_count."""
    from app.routers.chat import _save_assistant_message
    from app.database import async_session

    conv_id = str(uuid.uuid4())
    msg_id = str(uuid.uuid4())
    async with async_session() as db:
        db.add(Conversation(id=conv_id, title="ctx test2", kb_id=None, user_id=None))
        await db.commit()

    await _save_assistant_message(conv_id, "v1", [], False, msg_id=msg_id, prompt_tokens=10)
    await _save_assistant_message(conv_id, "v2", [], False, msg_id=msg_id, prompt_tokens=20)

    async with async_session() as db:
        row = (await db.execute(select(Message).where(Message.id == msg_id))).scalar_one()
        assert row.content == "v2"
        assert row.token_count == 20
