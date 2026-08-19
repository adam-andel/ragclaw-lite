# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
