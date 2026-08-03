"""Parse LLM cron payloads and compute next run times."""

import json
import logging
import re
from datetime import datetime, timezone

import pytz
from croniter import croniter

logger = logging.getLogger("ragclaw.cron")


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences and surrounding whitespace."""
    text = re.sub(r'```(?:json)?\s*|```', '', text).strip()
    return text


def _extract_json_objects(text: str) -> list[str]:
    """Return candidate JSON object substrings, most-likely-first.

    The greedy ``{...}`` match can swallow surrounding prose; we also collect
    narrower objects anchored on the cron fields so a partial greedy match does
    not cause the whole parse to fail.
    """
    candidates: list[str] = []
    greedy = re.search(r'\{[\s\S]*\}', text)
    if greedy:
        candidates.append(greedy.group(0))
    for m in re.finditer(r'\{[^{}]*"cron_expr"[^{}]*\}', text):
        candidates.append(m.group(0))
    return candidates


def try_parse_cron_payload(text: str) -> dict | None:
    """Detect a cron creation JSON returned by the LLM.

    Accepts either schema the model may emit:
      - {"type": "cron", "name", "cron_expr", "task_content", ...}
      - {"tool": "create_cron_job", "name", "cron_expr", "task_content", ...}
    Code fences and surrounding prose are tolerated; the first JSON object that
    carries the required fields wins.
    """
    cleaned = _strip_code_fences(text)
    if not cleaned:
        return None

    for snippet in _extract_json_objects(cleaned):
        try:
            data = json.loads(snippet)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        # Normalize the alternate schema to the canonical cron shape.
        if data.get("tool") == "create_cron_job":
            data.setdefault("type", "cron")
        if data.get("type") != "cron":
            continue

        cron_expr = (data.get("cron_expr") or "").strip()
        if not cron_expr or not croniter.is_valid(cron_expr):
            logger.warning("Invalid cron expression in LLM payload: %s", cron_expr)
            continue

        task_content = (data.get("task_content") or "").strip()
        if not task_content:
            logger.warning("Cron payload missing task_content")
            continue

        payload = {
            "name": (data.get("name") or "未命名任务").strip(),
            "cron_expr": cron_expr,
            "max_runs": data.get("max_runs"),
            "task_content": task_content,
            "description": (data.get("description") or "").strip(),
        }

        # Normalize infinite runs
        if payload["max_runs"] in (None, 0, -1):
            payload["max_runs"] = None
        else:
            try:
                payload["max_runs"] = int(payload["max_runs"])
                if payload["max_runs"] < 0:
                    payload["max_runs"] = None
            except (TypeError, ValueError):
                payload["max_runs"] = None

        return payload

    return None


def compute_next_run(cron_expr: str, tz_name: str = "UTC") -> datetime | None:
    """Compute the next UTC datetime for a cron expression."""
    try:
        tz = pytz.timezone(tz_name)
    except pytz.UnknownTimeZoneError:
        logger.warning("Unknown timezone %s, falling back to UTC", tz_name)
        tz = pytz.UTC

    local_now = datetime.now(tz)
    itr = croniter(cron_expr, local_now)
    next_local = itr.get_next(datetime)
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)
