"""In-process async scheduler for cron jobs."""

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
    """Background task: wake up periodically and run due cron jobs."""
    logger.info("Cron scheduler started")
    while True:
        try:
            await _tick()
        except Exception as e:
            logger.exception("Cron scheduler tick failed: %s", e)
        await asyncio.sleep(TICK_INTERVAL_SECONDS)


async def _tick():
    """Fetch and trigger all due scheduled jobs."""
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
        # Fire-and-forget each job execution.
        asyncio.create_task(_execute_job(job.id))


async def _execute_job(cron_job_id: str):
    """Execute a single cron job and update its state."""
    try:
        await execute_and_record_cron_job(cron_job_id)
    except Exception as e:
        logger.exception("Scheduled cron job %s failed: %s", cron_job_id, e)
