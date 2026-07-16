"""Backend-side signer for the MCP REPL sandbox identity envelope.

The REPL sandbox (``mcp/repl_mcp_server.py``) can run each user's generated
code under a dedicated, unprivileged Linux account. To do that safely it needs
a *trusted* user identity — one the client cannot forge. The Backend already
authenticated the session, so it signs the user id with a shared HMAC secret
and the sandbox only trusts a valid signature.

Envelope (sent inside a tool call's ``arguments``):
    {"user": "<id>", "exp": <int>, "sig": "<hex>"}
    sig = HMAC-SHA256(secret, f"{user}|{exp}")      (exp = 0 means no expiry)

When no shared secret is configured the function returns ``None`` and the
Backend falls back to the sandbox's single-account mode (auth disabled there
too), so this module is a no-op until ``REPL_AUTH_SECRET`` is deployed.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.services.config_manager import config_manager

# Tools executed inside the REPL sandbox that require the identity envelope.
REPL_AUTH_TOOLS = frozenset({"run_python", "run_shell", "run_javascript"})


def build_auth_envelope(user_id: str) -> dict | None:
    """Build the signed identity envelope for ``user_id``.

    Returns ``None`` when:
      * no ``repl_auth_secret`` is configured (single-account fallback), or
      * ``user_id`` is empty/unresolvable (caller must surface the error).

    The secret is read from ``config_manager`` (the runtime DB value, which is
    auto-generated on first boot and editable in the system settings UI), not
    from the static env — so the Backend and the MCP server stay in sync after
    an admin rotates the secret in the UI.
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

    msg = f"{user_id}|{exp}".encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return {"user": user_id, "exp": exp, "sig": sig}
