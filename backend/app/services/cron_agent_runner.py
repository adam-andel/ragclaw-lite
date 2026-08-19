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
"""Run cron jobs through the LangGraph ToolNode-based cron subgraph.

Concurrency safety: an in-memory asyncio.Lock per job_id prevents the same
job from being executed in parallel.  The lock is held for the entire
execution lifespan (LLM call included).  If a caller attempts to run a job
that is already running, the call is rejected immediately.
"""

import asyncio
import logging
from datetime import datetime, timezone

from app.database import async_session
from app.models.cron_job import CronJob, CronJobRun, CronJobStatus
from app.services.cron_graph import run_cron_execution_subgraph
from app.services.cron_parser import compute_next_run
from app.services.notification import create_cron_job_notification

logger = logging.getLogger("ragclaw.cron")

# ── Per-job concurrency guard ────────────────────────────────────────────────
_job_locks: dict[str, asyncio.Lock] = {}
_lock_registry_lock = asyncio.Lock()


async def _get_job_lock(cron_job_id: str) -> asyncio.Lock:
    """Return (or create) the asyncio.Lock for the given cron job id."""
    async with _lock_registry_lock:
        if cron_job_id not in _job_locks:
            _job_locks[cron_job_id] = asyncio.Lock()
        return _job_locks[cron_job_id]


async def execute_and_record_cron_job(cron_job_id: str) -> dict:
    """Run a cron job through the ToolNode subgraph and persist the result.

    Acquires an in-memory lock for *cron_job_id* so that a single job can
    never have two concurrent executions.  Additionally checks the persisted
    status: if the job is already RUNNING or COMPLETED the call is rejected
    with a descriptive result instead of creating a duplicate CronJobRun.

    The subgraph returns its final answer as plain text; this function
    persists that text into both CronJobRun.output (execution audit log) and
    CronJob.last_result (latest result).  It also manages next_run_at and
    status transitions.
    """
    lock = await _get_job_lock(cron_job_id)

    # Try-lock: if another coroutine is already inside the critical section
    # we bail out immediately rather than queueing up behind it.
    if lock.locked():
        logger.info("Cron job %s is already running; skipping duplicate trigger", cron_job_id)
        return {
            "output": None,
            "result": None,
            "status": "skipped",
            "error": "Job is already running",
        }

    async with lock:
        return await _execute_and_record_locked(cron_job_id)


async def _execute_and_record_locked(cron_job_id: str) -> dict:
    """Internal: execute a cron job while holding the per-job lock."""
    async with async_session() as db:
        job = await db.get(CronJob, cron_job_id)
        if not job:
            raise ValueError(f"Cron job {cron_job_id} not found")

        # Status guard: refuse to run a job that is already in a terminal or
        # already-running state.  This closes the gap between the lock check
        # and the DB read for cases where the lock was just freed by a
        # completed run.
        if job.status == CronJobStatus.RUNNING:
            logger.warning(
                "Cron job %s status is RUNNING inside lock; rejecting",
                cron_job_id,
            )
            return {
                "output": None,
                "result": None,
                "status": "skipped",
                "error": "Job is already running (status guard)",
            }
        if job.status == CronJobStatus.COMPLETED:
            logger.info("Cron job %s is COMPLETED; skipping", cron_job_id)
            return {
                "output": None,
                "result": None,
                "status": "skipped",
                "error": "Job has already completed",
            }

        previous_status = job.status
        job.status = CronJobStatus.RUNNING
        await db.commit()

        run = CronJobRun(cron_job_id=job.id, status="running")
        db.add(run)
        await db.commit()

        output = None
        try:
            output = await run_cron_execution_subgraph(job)
            run.status = "executed"
            run.output = output
            job.last_result = output
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

            # Refresh job because the subgraph may have updated it via tool calls
            # (e.g. when running through providers without native function calling).
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
            "output": output if run.status == "executed" else None,
            "result": job.last_result,
            "status": run.status,
            "error": run.error,
        }
