"""Run cron jobs through the LangGraph ToolNode-based cron subgraph."""

import logging
from datetime import datetime, timezone

from app.database import async_session
from app.models.cron_job import CronJob, CronJobRun, CronJobStatus
from app.services.cron_graph import run_cron_execution_subgraph
from app.services.cron_parser import compute_next_run
from app.services.notification import create_cron_job_notification

logger = logging.getLogger("erag.cron")


async def execute_and_record_cron_job(cron_job_id: str) -> dict:
    """Run a cron job through the ToolNode subgraph and persist the result.

    The subgraph itself calls record_cron_result_tool to update CronJob.last_result.
    This function additionally creates a CronJobRun audit log and manages
    next_run_at / status transitions.
    """
    async with async_session() as db:
        job = await db.get(CronJob, cron_job_id)
        if not job:
            raise ValueError(f"Cron job {cron_job_id} not found")

        previous_status = job.status
        job.status = CronJobStatus.RUNNING
        await db.commit()

        run = CronJobRun(cron_job_id=job.id, status="running")
        db.add(run)
        await db.commit()

        output = None
        try:
            output = await run_cron_execution_subgraph(job)
            run.status = "success"
            run.output = output
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # Refresh job because the subgraph updated last_result via tool.
            await db.refresh(job)
            job.run_count += 1
            job.last_run_at = run.finished_at
            job.last_error = None

            if job.max_runs and job.run_count >= job.max_runs:
                job.status = CronJobStatus.COMPLETED
                job.next_run_at = None
            elif previous_status in (CronJobStatus.SCHEDULED, CronJobStatus.RUNNING):
                job.next_run_at = compute_next_run(job.cron_expr, job.timezone)
                job.status = CronJobStatus.SCHEDULED
            else:
                # Manual run from paused/failed state: keep previous status.
                job.status = previous_status if previous_status != CronJobStatus.RUNNING else CronJobStatus.SCHEDULED

        except Exception as e:
            logger.exception("Cron job %s execution failed", cron_job_id)
            run.status = "failed"
            run.error = str(e)
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.status = CronJobStatus.FAILED
            job.last_error = str(e)[:2000]

        await db.commit()

        await create_cron_job_notification(
            db,
            job=job,
            status=run.status,
            result=job.last_result,
            error=run.error,
        )

        return {
            "output": output if run.status == "success" else None,
            "result": job.last_result,
            "status": run.status,
            "error": run.error,
        }
