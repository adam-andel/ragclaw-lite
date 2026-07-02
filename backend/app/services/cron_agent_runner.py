"""Run an isolated agent session for a scheduled cron job."""

import json
import logging
from datetime import datetime, timezone

from app.database import async_session
from app.models.cron_job import CronJob, CronJobRun, CronJobStatus
from app.models.user import User
from app.services.agent_graph import erag_agent_graph
from app.services.agent_nodes import _extract_download_links_from_state
from app.services.cron_parser import (
    build_cron_query, extract_cron_result, remove_cron_result_marker, compute_next_run,
)
from app.services.llm_client import llm_client

logger = logging.getLogger("erag.cron")


async def run_cron_agent(job: CronJob) -> str:
    """Execute a cron job in an isolated agent session.

    Reuses the same LangGraph flow and LLM client as chat.py but uses a
    non-streaming completion. The job payload is wrapped with a result marker
    so the scheduler can extract the execution summary afterwards.
    """
    tenant_id = None
    if job.user_id:
        async with async_session() as db:
            user = await db.get(User, job.user_id)
            if user:
                tenant_id = user.tenant_id

    initial_state = {
        "query": build_cron_query(job.task_content, job.id),
        "kb_id": job.kb_id or "",
        "skill_id": job.skill_id or "",
        "user_id": job.user_id or "",
        "tenant_id": tenant_id or "",
        "conversation_history": [],
        "active_skill": None,
        "available_tools": [],
        "rag_context": "",
        "citations": [],
        "memory_context": "",
        "tool_calls": None,
        "tool_round": 0,
        "tool_results": [],
        "tool_messages": [],
        "cache_hit": False,
        "final_answer": "",
        "retrieval_ms": 0,
    }

    state = await erag_agent_graph.run(initial_state)

    messages = erag_agent_graph.build_generation_messages(state)

    response = await llm_client.chat(messages=messages, temperature=0.3, max_tokens=4096)

    # Append system-generated download links from tool results, matching chat.py behavior.
    download_links = _extract_download_links_from_state(state)
    if download_links:
        response = response + download_links

    return response


async def execute_and_record_cron_job(cron_job_id: str) -> dict:
    """Run a cron job, persist a run log, and update the job state.

    This is the shared execution path used by both the scheduler and the
    manual "run now" endpoint.

    Returns a summary dict with status, result, and parsed marker.
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
            output = await run_cron_agent(job)
            marker = extract_cron_result(output)

            run.status = "success"
            run.output = output
            run.result_json = json.dumps(marker, ensure_ascii=False) if marker else None
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)

            job.run_count += 1
            job.last_run_at = run.finished_at
            if marker:
                job.last_result = marker.get("cron_result", "")[:2000]
            else:
                job.last_result = remove_cron_result_marker(output)[:2000]
            job.last_error = None

            if job.max_runs and job.run_count >= job.max_runs:
                job.status = CronJobStatus.COMPLETED
                job.next_run_at = None
            elif previous_status in (CronJobStatus.SCHEDULED, CronJobStatus.RUNNING):
                job.next_run_at = compute_next_run(job.cron_expr, job.timezone)
                job.status = CronJobStatus.SCHEDULED
            else:
                # Manual run from paused/failed state: keep paused unless scheduler enables it.
                job.status = previous_status if previous_status != CronJobStatus.RUNNING else CronJobStatus.SCHEDULED

        except Exception as e:
            logger.exception("Cron job %s execution failed", cron_job_id)
            run.status = "failed"
            run.error = str(e)
            run.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
            job.status = CronJobStatus.FAILED
            job.last_error = str(e)[:2000]

        await db.commit()

        return {
            "output": output if run.status == "success" else None,
            "result": job.last_result,
            "status": run.status,
            "error": run.error,
        }
