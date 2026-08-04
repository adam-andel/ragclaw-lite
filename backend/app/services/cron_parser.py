"""Compute next run times for cron expressions."""

import logging
from datetime import datetime, timezone

import pytz
from croniter import croniter

logger = logging.getLogger("ragclaw.cron")


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
