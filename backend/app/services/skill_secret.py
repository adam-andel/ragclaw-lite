"""Secret-zero: push per-skill API keys to the ragclaw-egress injection proxy.

The REPL sandbox must NEVER hold a real skill API KEY (FS / env / process memory).
Instead, the KEY lives in exactly two places:
  1. backend ``config.enc`` (encrypted static storage), and
  2. the injection proxy's memory inside the ragclaw-egress container.

When a skill needs a KEY, the backend pushes ``{folder, upstream_base,
header_format, api_key}`` to the proxy over ``PUT /secret``. The proxy then
injects the KEY into upstream requests on behalf of the sandbox (see
``mcp/skill_secret_proxy.py``). The sandbox only ever talks to the internal
``http://ragclaw-egress:9090/<proxy_path>/...`` endpoint — the KEY never
traverses the sandbox.

See ``data/docs/secret-zero-skill-apikey-design.md``.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from app.config import settings
from app.services import config_manager
from app.services.skill_manager import get_skill_dir, run_skill_init_script

logger = logging.getLogger("ragclaw.skill_secret")

# Internal-network URL(s) of the injection proxy. The proxy is reachable only
# from the backend / ragclaw-internal network — never published to a host port.
_SECRET_PROXY_URLS = [
    settings.ragclaw_egress_secret_url,
    "http://127.0.0.1:9090",
]


async def _put_secret(payload: dict) -> bool:
    """Best-effort PUT /secret to the injection proxy. Returns True on 2xx."""
    last_err = None
    for url in _SECRET_PROXY_URLS:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.put(url.rstrip("/") + "/secret", json=payload)
                if resp.status_code == 200:
                    logger.info("pushed skill secret to %s ok folder=%s",
                                url, payload.get("folder"))
                    return True
                logger.warning("put secret to %s -> HTTP %d folder=%s",
                               url, resp.status_code, payload.get("folder"))
        except Exception as e:  # proxy down / unreachable
            last_err = e
            logger.warning("put secret to %s failed: %s", url, e)
    if last_err:
        logger.warning("all skill-secret push targets unreachable: %s", last_err)
    return False


async def _delete_secret(folder: str) -> bool:
    """Best-effort DELETE /secret?folder=<folder>. Returns True on 2xx/404."""
    last_err = None
    for url in _SECRET_PROXY_URLS:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.delete(url.rstrip("/") + "/secret",
                                           params={"folder": folder})
                if resp.status_code in (200, 204, 404):
                    logger.info("deleted skill secret at %s folder=%s", url, folder)
                    return True
                logger.warning("delete secret at %s -> HTTP %d folder=%s",
                               url, resp.status_code, folder)
        except Exception as e:
            last_err = e
            logger.warning("delete secret at %s failed: %s", url, e)
    if last_err:
        logger.warning("all skill-secret delete targets unreachable: %s", last_err)
    return False


def _adapter_for(folder: str) -> dict | None:
    """Read the skill's ragclaw-owned adapter.json (describes upstream/KEY shape)."""
    adapter_path = get_skill_dir(folder) / ".ragclaw" / "adapter.json"
    if not adapter_path.is_file():
        return None
    try:
        return json.loads(adapter_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("could not read adapter.json for %s: %s", folder, e)
        return None


async def push_skill_secret(folder: str) -> bool:
    """Push a configured skill's API KEY to the injection proxy (no-op if unset)."""
    api_key = config_manager.get_skill_api_key(folder)
    if not api_key:
        return False
    adapter = _adapter_for(folder)
    if not adapter:
        # KEY is stored but this skill ships no ragclaw adapter — the sandbox
        # cannot route through the proxy, so there is nothing to push. Keep the
        # KEY in config; init will fall back to vanilla for this skill.
        logger.warning("skill %s has an API KEY but no .ragclaw/adapter.json; "
                       "proxy injection skipped (vanilla fallback)", folder)
        return False
    payload = {
        "folder": folder,
        "proxy_path": adapter.get("proxy_path", folder),
        "upstream_base": adapter.get("upstream_base", ""),
        "header_format": adapter.get("header_format", "Bearer {}"),
        "api_key": api_key,
    }
    return await _put_secret(payload)


async def set_skill_api_key(folder: str, api_key: str | None):
    """Configure (or clear, when empty/None) a skill's secret-zero API KEY.

    Persists to config.enc, pushes/deletes the KEY in the egress injection proxy,
    and re-runs the skill's init hook so the resolved command switches between the
    shim (KEY present) and the native CLI (vanilla). Triggers init even when the
    adapter is absent so the skill returns to a consistent state.
    """
    has_key = bool(api_key)
    config_manager.set_skill_api_key(folder, api_key if has_key else None)
    # Persist the merged namespace to encrypted config.enc. Use the RAW namespace —
    # get_config_safe() masks it to {} and would wipe every stored key.
    await config_manager.update({"skill_secrets": config_manager.get_skill_secrets_raw()})
    if has_key:
        pushed = await push_skill_secret(folder)
        if not pushed:
            logger.warning("skill %s API KEY stored but proxy push deferred "
                           "(proxy unreachable; will self-heal)", folder)
    else:
        await _delete_secret(folder)
    # Re-run init with the explicit status so the resolved command is correct even
    # if no api_key PATCH reached the init hook previously.
    run_skill_init_script(folder, api_key_status="set" if has_key else "empty")


# ── Self-healing (mirrors config.py REPL auth-secret retry) ───────────────────
# The proxy is a separate container that may restart (e.g. after a compose pull).
# On restart its in-memory KEY map is empty until the backend re-pushes. A
# background retry loop runs only while a push is pending, and stops itself on the
# first success — no perpetual heartbeat.
_secret_push_pending: bool = False
_secret_retry_task = None


async def ensure_skill_secrets_pushed(retries: int = 6, interval: float = 2.0) -> bool:
    """Startup helper: re-push every configured skill KEY to the proxy."""
    global _secret_push_pending
    secrets = config_manager.get_skill_secrets_raw()
    if not secrets:
        _secret_push_pending = False
        return True
    ok = True
    for folder in secrets:
        if not await push_skill_secret(folder):
            ok = False
    if ok:
        _secret_push_pending = False
        return True
    _secret_push_pending = True
    return False


async def _secret_retry_loop(interval: float = 60.0):
    global _secret_push_pending, _secret_retry_task
    _secret_push_pending = True
    while _secret_push_pending:
        try:
            secrets = config_manager.get_skill_secrets_raw()
            if not secrets:
                _secret_push_pending = False
                break
            all_ok = True
            for folder in secrets:
                if not await push_skill_secret(folder):
                    all_ok = False
            if all_ok:
                _secret_push_pending = False
                logger.info("skill-secret self-heal succeeded; stopping retry loop")
                _secret_retry_task = None
                return
        except Exception as e:  # pragma: no cover
            logger.warning("skill-secret retry failed: %s", e)
        await asyncio.sleep(interval)
    _secret_retry_task = None


async def ensure_secret_retry_running(interval: float = 60.0):
    """Start the self-heal retry loop if it is not already running."""
    global _secret_retry_task
    if _secret_retry_task is not None and not _secret_retry_task.done():
        return
    if not _secret_push_pending:
        return
    _secret_retry_task = asyncio.create_task(_secret_retry_loop(interval))
