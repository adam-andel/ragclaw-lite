"""Centralized logging setup for RAGClaw (P0: observability).

RAGClaw's own loggers (``ragclaw.*``) propagate to the *root* logger. By default
root has NO handler, so INFO-level records are silently dropped (only WARNING+
reach stderr via logging's last-resort handler). uvicorn may set
``disable_existing_loggers=True`` (marking our loggers disabled).
``setup_logging()`` re-enables disabled loggers, removes every root handler it
did not install, and attaches a single timestamped stderr handler to root so
ragclaw.* INFO logs become visible (and de-duplicated). Call it from the lifespan
startup and defensively at import time; the per-request middleware re-applies it
because uvicorn --reload can re-install handlers after lifespan.
"""

import logging
import os
import sys

# RAGClaw loggers we want at INFO by default.
_RAGCLAW_LOGGERS = (
    "ragclaw",
    "ragclaw.llm",
    "ragclaw.agent",
    "ragclaw.mcp",
    "ragclaw.tool_registry",
    "ragclaw.config",
    "ragclaw.cron",
    "ragclaw.skill_script",
)

# Marker on our own root handler. setup_logging() clears ALL root handlers and
# re-adds a fresh one, so this mark is mainly documentary now.
_RAGCLAW_HANDLER_MARK = "__ragclaw_stderr__"


def _is_production() -> bool:
    """True when running as a production build (no INFO-level ragclaw logs).

    Controlled by the RAGCLAW_ENV env var. Defaults to production (safe: INFO
    suppressed) so an unset variable never accidentally leaks verbose logs.
    Set RAGCLAW_ENV=development (or dev) in the dev stack to enable INFO.
    """
    env = os.environ.get("RAGCLAW_ENV", "production").strip().lower()
    return env not in ("development", "dev", "1", "true", "debug")


def setup_logging(level: int | None = None) -> None:
    """Ensure RAGClaw loggers emit to stderr at the right level.

    Safe to call multiple times. Fixes three logging pitfalls:
      1) uvicorn's ``disable_existing_loggers`` marks our loggers disabled — we
         re-enable them.
      2) Under ``uvicorn --reload`` the worker is forked and its ``sys.stderr``
         is later redirected, leaving any handler captured at import time
         (parent) bound to a dead stream. We always (re)bind to the CURRENT
         ``sys.stderr`` so logs actually reach the container's captured stderr
         (and thus ``docker logs``).
      3) dev prints INFO, production prints WARNING+ only (see _is_production).
    """
    if level is None:
        level = logging.WARNING if _is_production() else logging.INFO

    # 1) Re-enable any logger uvicorn disabled via disable_existing_loggers.
    for name in list(logging.root.manager.loggerDict):
        logging.getLogger(name).disabled = False

    # 2) Ensure root holds EXACTLY one handler: ours. Drop every existing root
    #    handler before re-adding a fresh one bound to the CURRENT sys.stderr.
    #    uvicorn --reload forks a worker and redirects sys.stderr, leaving a
    #    handler captured at import time bound to a dead stream.
    #    uvicorn's own startup/access logs are unaffected — they travel through
    #    uvicorn's dedicated handlers (uvicorn.error / uvicorn.access, which set
    #    propagate=False), not root.
    root = logging.root
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler._ragclaw_mark = _RAGCLAW_HANDLER_MARK
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    handler.setLevel(level)
    root.addHandler(handler)
    # Root must not pre-filter below the level we want emitted.
    if root.level == logging.NOTSET or root.level > level:
        root.setLevel(level)

    # 3) Explicitly raise RAGClaw loggers so records at `level` are emitted.
    for name in _RAGCLAW_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True
