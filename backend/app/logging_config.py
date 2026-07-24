"""Centralized logging setup for RAGClaw (P0: observability).

uvicorn installs its own logging config at startup, attaching handlers to the
``uvicorn`` logger only. RAGClaw's own loggers (``ragclaw.*``) propagate to the
*root* logger, which by default has NO handler, so INFO-level records are
silently dropped (only WARNING+ reach stderr via logging's last-resort handler).
Some uvicorn launch configs also set ``disable_existing_loggers=True``, which
marks our loggers disabled. ``setup_logging()`` fixes both: it re-enables any
disabled logger and attaches a stderr handler to root so ragclaw.* INFO logs
become visible. Call it from the lifespan startup (after uvicorn's config is
applied) and defensively at import time.
"""

import logging
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


def setup_logging(level: int = logging.INFO) -> None:
    """Ensure RAGClaw loggers emit to stderr (INFO and above).

    Safe to call multiple times. Re-enables loggers uvicorn may have disabled
    via ``disable_existing_loggers`` and adds a root stderr handler if missing.
    """
    # 1) Re-enable any logger uvicorn disabled via disable_existing_loggers.
    for name in list(logging.root.manager.loggerDict):
        logging.getLogger(name).disabled = False

    # 2) Ensure a root handler so ragclaw.* (propagate=True) reach stderr.
    if not logging.root.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logging.root.addHandler(handler)
    logging.root.setLevel(level)

    # 3) Explicitly raise RAGClaw loggers so INFO records are emitted.
    for name in _RAGCLAW_LOGGERS:
        lg = logging.getLogger(name)
        lg.setLevel(level)
        lg.propagate = True
