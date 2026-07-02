"""Parse LLM cron payloads, execution result markers, and compute next runs."""

import json
import logging
import re
from datetime import datetime, timezone

import pytz
from croniter import croniter

logger = logging.getLogger("erag.cron")

_CRON_RESULT_RE = re.compile(
    r'\{\s*"cron_id"\s*:\s*"([^"]+)"\s*,\s*"cron_result"\s*:\s*"([^"]*)"\s*\}'
)


def _strip_code_fences(text: str) -> str:
    """Remove markdown code fences and surrounding whitespace."""
    text = re.sub(r'```(?:json)?\s*|```', '', text).strip()
    return text


def try_parse_cron_payload(text: str) -> dict | None:
    """Detect a cron creation JSON returned by the LLM.

    Expected shape:
        {
            "type": "cron",
            "name": "...",
            "cron_expr": "0 9 * * *",
            "max_runs": 1 | null,
            "task_content": "...",
            "description": "..."
        }
    """
    cleaned = _strip_code_fences(text)
    obj_match = re.search(r'\{[\s\S]*\}', cleaned)
    if not obj_match:
        return None
    cleaned = obj_match.group(0)

    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None

    if data.get("type") != "cron":
        return None

    cron_expr = data.get("cron_expr", "")
    if not cron_expr or not croniter.is_valid(cron_expr):
        logger.warning("Invalid cron expression in LLM payload: %s", cron_expr)
        return None

    payload = {
        "name": (data.get("name") or "未命名任务").strip(),
        "cron_expr": cron_expr.strip(),
        "max_runs": data.get("max_runs"),
        "task_content": (data.get("task_content") or "").strip(),
        "description": (data.get("description") or "").strip(),
    }

    if not payload["task_content"]:
        logger.warning("Cron payload missing task_content")
        return None

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


def extract_cron_result(text: str) -> dict | None:
    """Extract the execution result marker from an LLM output.

    Marker format:
        {"cron_id": "<uuid>", "cron_result": "<summary>"}
    """
    match = _CRON_RESULT_RE.search(text)
    if not match:
        return None
    return {
        "cron_id": match.group(1),
        "cron_result": match.group(2),
    }


def remove_cron_result_marker(text: str) -> str:
    """Return the text with the cron result marker line removed."""
    return _CRON_RESULT_RE.sub("", text).strip()


def build_cron_query(task_content: str, cron_job_id: str) -> str:
    """Append the result-capture marker to a task prompt."""
    return (
        f"{task_content}\n\n"
        "When you finish the task, append a final line exactly as this JSON object "
        "(no markdown fences, no extra text in the same line):\n"
        f'{{"cron_id": "{cron_job_id}", "cron_result": "<concise summary of the result>"}}'
    )


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
