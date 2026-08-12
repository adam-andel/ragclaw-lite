"""Lazily materialise a per-user symlink to a skill's resources inside the
user's mcp-repl sandbox.

When the Backend selects/loads a skill for a conversation
(``agent_nodes._load_skill_body_and_tools``), it asks mcp-repl to create, for
that *one* user, a symlink ``<sandbox>/.ragclaw/skills/<name>`` pointing at the
shared ``enable/<name>`` entry. This is the only write the Backend triggers —
and only for the single skill actually in use (not a per-user full sync), so
write pressure stays O(used skills).

The call is best-effort and NEVER raises: any failure (no sandbox, skill
disabled, auth missing, transport error) degrades silently. The skill body is
always loaded from the shared store regardless, so a missing in-sandbox file
view must not break the agent graph.
"""
from __future__ import annotations

import logging

import httpx

from app.config import settings
from app.services.config_manager import config_manager
from app.services.repl_auth import get_user_repl_uid

logger = logging.getLogger("ragclaw.skill_link")

_AUTH_NOT_CONFIGURED = '{"error": "auth not configured"}'


async def ensure_user_skill_link(user_id: str, folder_name: str) -> bool:
    """Ensure the user's sandbox exposes ``folder_name``'s skill resources.

    Returns True if the link was confirmed present (created or already there),
    False if it could not be (no sandbox, skill disabled, auth missing, or any
    transport error). Callers must not depend on this for the skill to function.
    """
    uid = await get_user_repl_uid(user_id)
    if uid is None:
        logger.debug("ensure_user_skill_link: no sandbox uid for user=%s", user_id)
        return False
    secret = config_manager.repl_auth_secret
    if not secret:
        logger.warning("ensure_user_skill_link: REPL auth secret missing")
        return False

    url = f"{settings.mcp_repl_internal_url.rstrip('/')}/skills/link"
    headers = {"X-Repl-Auth": secret, "X-Repl-Uid": str(uid)}
    body = {"name": folder_name}

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(url, headers=headers, json=body)
            if _is_auth_not_configured(resp):
                # mcp-repl lost its in-memory secret (e.g. just restarted).
                # Re-push once and replay, matching the workspace proxy path.
                logger.warning(
                    "ensure_user_skill_link: auth not configured name=%s — re-pushing secret",
                    folder_name,
                )
                try:
                    from app.services.mcp_client import _repush_repl_auth_secret

                    if await _repush_repl_auth_secret():
                        resp = await client.post(url, headers=headers, json=body)
                except Exception as e:  # noqa: BLE001
                    logger.warning("ensure_user_skill_link: repush failed: %s", e)
            if resp.status_code == 404:
                # Skill disabled or unknown — nothing to expose.
                logger.info("ensure_user_skill_link: skill not enabled name=%s", folder_name)
                return False
            if resp.status_code >= 400:
                logger.warning(
                    "ensure_user_skill_link: mcp-repl returned %s name=%s",
                    resp.status_code, folder_name,
                )
                return False
            return True
    except httpx.ConnectError:
        logger.warning("ensure_user_skill_link: mcp-repl unavailable name=%s", folder_name)
        return False
    except Exception as e:  # noqa: BLE001
        logger.warning("ensure_user_skill_link: error name=%s: %s", folder_name, e)
        return False


def _is_auth_not_configured(resp) -> bool:
    if resp.status_code != 403:
        return False
    try:
        text = resp.text.strip()
    except Exception:
        return False
    return text == _AUTH_NOT_CONFIGURED or '"auth not configured"' in text
