"""Backend-side signer for the MCP REPL sandbox identity envelope.

The REPL sandbox (``mcp/repl_mcp_server.py``) can run each user's generated
code under a dedicated, unprivileged Linux account. To do that safely it needs
a *trusted* user identity — one the client cannot forge. The Backend already
authenticated the session, so it signs the user id (and, when available, the
user's dedicated sandbox UID) with a shared HMAC secret and the sandbox only
trusts a valid signature.

Envelope (sent inside a tool call's ``arguments``):
    {"user": "<id>", "exp": <int>, "sig": "<hex>"}
    sig = HMAC-SHA256(secret, f"{user}|{exp}")            (legacy: no uid)
    sig = HMAC-SHA256(secret, f"{user}|{uid}|{exp}")      (with dedicated uid)

When ``repl_uid`` is known (new users), it is signed into the envelope so the
sandbox can drop privileges to that exact UID **statelessly** (no DB lookup on
the sandbox side). Legacy users without a stored UID omit the field and the
sandbox falls back to its deterministic hash mapping, so existing directories
keep working. ``exp = 0`` means the envelope never expires.

When no shared secret is configured the function returns ``None`` and the
Backend falls back to the sandbox's single-account mode (auth disabled there
too), so this module is a no-op until ``REPL_AUTH_SECRET`` is deployed.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.config import settings
from app.services.config_manager import config_manager

# Tools executed inside the REPL sandbox that require the identity envelope.
REPL_AUTH_TOOLS = frozenset({"run_python", "run_shell", "run_javascript"})


def build_auth_envelope(user_id: str, repl_uid: int | None = None) -> dict | None:
    """Build the signed identity envelope for ``user_id``.

    Returns ``None`` when:
      * no ``repl_auth_secret`` is configured (single-account fallback), or
      * ``user_id`` is empty/unresolvable (caller must surface the error).

    When ``repl_uid`` is provided it is included in both the signed message and
    the envelope payload, letting the sandbox resolve the exact isolation UID
    without a database lookup. The secret is read from ``config_manager`` (the
    runtime DB value, auto-generated on first boot and editable in the system
    settings UI), so the Backend and the MCP server stay in sync after an admin
    rotates the secret in the UI.
    """
    secret = config_manager.repl_auth_secret
    if not secret:
        return None

    user_id = (user_id or "").strip()
    if not user_id:
        # Refusing to sign an unattributed identity keeps the sandbox's
        # fail-closed behaviour intact (no envelope => rejected when auth on).
        return None

    exp_seconds = settings.repl_auth_exp_seconds
    exp = int(time.time()) + int(exp_seconds) if exp_seconds and exp_seconds > 0 else 0

    parts = [user_id]
    if repl_uid is not None:
        parts.append(str(int(repl_uid)))
    parts.append(str(exp))
    msg = "|".join(parts).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()

    envelope = {"user": user_id, "exp": exp, "sig": sig}
    if repl_uid is not None:
        envelope["uid"] = int(repl_uid)
    return envelope


async def get_user_repl_uid(user_id: str) -> int | None:
    """Resolve a user's stored REPL sandbox UID (or ``None``).

    Returns ``None`` when the user has no dedicated UID yet (legacy users that
    still rely on the sandbox's hash fallback) or on any lookup error — the
    caller then omits the ``uid`` field and the sandbox applies its fallback.
    Uses a late-bound async session so this signer module stays lightweight and
    free of import-time database coupling.
    """
    if not user_id:
        return None
    try:
        from app.database import async_session
        from app.models.user import User
        from sqlalchemy import select

        async with async_session() as session:
            result = await session.execute(select(User.repl_uid).where(User.id == user_id))
            row = result.first()
        return row[0] if row else None
    except Exception:
        # Degrade to "no uid" rather than break the whole tool call.
        return None
