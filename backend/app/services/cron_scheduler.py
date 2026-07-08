"""In-process async scheduler for cron jobs.

Concurrency model:
- A single scheduler loop ticks every 60 seconds.
- Each tick sequentially processes due jobs one at a time via
  ``execute_and_record_cron_job``, which internally holds a per-job
  ``asyncio.Lock`` for the full execution lifespan.
- This guarantees that a given cron job is never executed concurrently,
  whether the trigger comes from the scheduler or from a manual API call.
"""

import asyncio
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import async_session
from app.models.cron_job import CronJob, CronJobStatus
from app.services.cron_agent_runner import execute_and_record_cron_job

logger = logging.getLogger("erag.cron")

TICK_INTERVAL_SECONDS = 60


async def scheduler_loop():
    """Background task: wake up periodically and run due cron jobs.

    Jobs are processed one at a time to keep the scheduler predictable.
    The per-job lock inside ``execute_and_record_cron_job`` is still the
    authoritative concurrency guard — this loop simply avoids creating a
    flood of ``create_task`` fire-and-forget calls.
    """
    logger.info("Cron scheduler started")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.exception("Cron scheduler tick failed: %s", e)
        await asyncio.sleep(TICK_INTERVAL_SECONDS)


async def _tick():
    """Fetch and sequentially execute all due scheduled jobs."""
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as db:
        result = await db.execute(
            select(CronJob)
            .where(
                CronJob.status == CronJobStatus.SCHEDULED,
                CronJob.next_run_at <= now,
            )
            .order_by(CronJob.next_run_at)
        )
        jobs = result.scalars().all()

    for job in jobs:
        try:
            await execute_and_record_cron_job(job.id)
        except Exception as e:
            logger.exception("Cron job %s execution threw: %s", job.id, e)
