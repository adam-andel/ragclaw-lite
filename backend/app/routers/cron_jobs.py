"""Cron job management API routes."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.cron_job import CronJob, CronJobRun, CronJobStatus
from app.services.auth import get_current_user
from app.services.cron_parser import compute_next_run
from app.services.cron_agent_runner import execute_and_record_cron_job
from app.schemas.cron_job import (
    CronJobCreate,
    CronJobUpdate,
    CronJobResponse,
    CronJobListResponse,
    CronJobRunResponse,
    CronJobRunListResponse,
)

router = APIRouter(prefix="/api/cron-jobs", tags=["Cron Jobs"])


def _cron_job_response(job: CronJob) -> CronJobResponse:
    return CronJobResponse(
        id=job.id,
        tenant_id=job.tenant_id,
        user_id=job.user_id,
        name=job.name,
        description=job.description,
        cron_expr=job.cron_expr,
        timezone=job.timezone,
        max_runs=job.max_runs,
        run_count=job.run_count,
        task_content=job.task_content,
        kb_id=job.kb_id,
        skill_id=job.skill_id,
        workspace_dir=job.workspace_dir,
        status=job.status.value,
        next_run_at=job.next_run_at.isoformat() if job.next_run_at else None,
        last_run_at=job.last_run_at.isoformat() if job.last_run_at else None,
        last_result=job.last_result,
        last_error=job.last_error,
        created_at=job.created_at.isoformat() if job.created_at else None,
        updated_at=job.updated_at.isoformat() if job.updated_at else None,
    )


def _cron_job_run_response(run: CronJobRun) -> CronJobRunResponse:
    return CronJobRunResponse(
        id=run.id,
        cron_job_id=run.cron_job_id,
        started_at=run.started_at.isoformat() if run.started_at else None,
        finished_at=run.finished_at.isoformat() if run.finished_at else None,
        status=run.status,
        output=run.output,
        result_json=run.result_json,
        error=run.error,
    )


@router.get("", response_model=CronJobListResponse)
async def list_cron_jobs(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    status: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List cron jobs.

    Admin sees all jobs; other roles see only their own jobs
    (where CronJob.user_id == current_user.id).

    Optional `search` filters by job name or description.
    Optional `status` filters by exact runtime state
    (scheduled / running / paused / failed / completed).
    """
    conditions = []
    if current_user.role.value != "admin":
        conditions.append(CronJob.user_id == current_user.id)
    if search:
        conditions.append((CronJob.name.ilike(f"%{search}%")) | (CronJob.description.ilike(f"%{search}%")))
    if status is not None:
        try:
            status_val = CronJobStatus(status)
        except ValueError:
            raise HTTPException(400, f"无效的定时任务状态: {status}")
        conditions.append(CronJob.status == status_val)

    count_q = select(func.count()).select_from(CronJob)
    if conditions:
        count_q = count_q.where(*conditions)
    total = (await db.execute(count_q)).scalar() or 0

    items_q = select(CronJob).order_by(CronJob.created_at.desc())
    if conditions:
        items_q = items_q.where(*conditions)
    items_q = items_q.offset((page - 1) * size).limit(size)
    items = (await db.execute(items_q)).scalars().all()

    return CronJobListResponse(
        items=[_cron_job_response(job) for job in items],
        total=total,
        page=page,
        size=size,
    )


@router.post("", response_model=CronJobResponse, status_code=201)
async def create_cron_job(
    data: CronJobCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Create a cron job manually from the management UI."""
    next_run = compute_next_run(data.cron_expr, data.timezone or "UTC")

    job = CronJob(
        tenant_id=current_user.tenant_id,
        user_id=current_user.id,
        name=data.name,
        description=data.description,
        cron_expr=data.cron_expr,
        timezone=data.timezone or "UTC",
        max_runs=data.max_runs,
        task_content=data.task_content,
        kb_id=data.kb_id,
        skill_id=data.skill_id,
        status=CronJobStatus.SCHEDULED,
        next_run_at=next_run,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)
    return _cron_job_response(job)


@router.get("/{job_id}", response_model=CronJobResponse)
async def get_cron_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single cron job."""
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")
    return _cron_job_response(job)


@router.patch("/{job_id}", response_model=CronJobResponse)
async def update_cron_job(
    job_id: str,
    data: CronJobUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Update a cron job."""
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    if data.name is not None:
        job.name = data.name
    if data.description is not None:
        job.description = data.description
    if data.cron_expr is not None:
        job.cron_expr = data.cron_expr
    if data.timezone is not None:
        job.timezone = data.timezone
    if data.max_runs is not None:
        job.max_runs = data.max_runs
    if data.task_content is not None:
        job.task_content = data.task_content
    if data.kb_id is not None:
        job.kb_id = data.kb_id
    if data.skill_id is not None:
        job.skill_id = data.skill_id

    # Recalculate next run if schedule-related fields changed.
    if any(f is not None for f in (data.cron_expr, data.timezone, data.max_runs)):
        if job.status in (CronJobStatus.SCHEDULED, CronJobStatus.PAUSED, CronJobStatus.FAILED):
            job.next_run_at = compute_next_run(job.cron_expr, job.timezone)
            if job.status == CronJobStatus.FAILED:
                job.status = CronJobStatus.SCHEDULED
            job.last_error = None

    job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    return _cron_job_response(job)


@router.delete("/{job_id}")
async def delete_cron_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Delete a cron job and its run logs."""
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    await db.execute(delete(CronJobRun).where(CronJobRun.cron_job_id == job_id))
    await db.delete(job)
    await db.commit()
    return {"status": "deleted"}


@router.post("/{job_id}/toggle", response_model=CronJobResponse)
async def toggle_cron_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Pause or resume a cron job."""
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    if job.status == CronJobStatus.SCHEDULED:
        job.status = CronJobStatus.PAUSED
        job.next_run_at = None
    elif job.status in (CronJobStatus.PAUSED, CronJobStatus.FAILED):
        job.status = CronJobStatus.SCHEDULED
        job.next_run_at = compute_next_run(job.cron_expr, job.timezone)
        job.last_error = None
    else:
        raise HTTPException(400, f"当前状态无法切换: {job.status.value}")

    job.updated_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(job)
    return _cron_job_response(job)


@router.post("/{job_id}/run-now")
async def run_cron_job_now(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a cron job immediately, outside its normal schedule.

    Refuses to start a new execution when the job is already RUNNING or
    COMPLETED.  For RUNNING jobs this prevents duplicate concurrent LLM
    calls; for COMPLETED jobs the user must reset the job first.
    """
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    if job.status == CronJobStatus.RUNNING:
        raise HTTPException(409, "任务正在执行中，请等待当前执行完成")
    if job.status == CronJobStatus.COMPLETED:
        raise HTTPException(400, "任务已完成，请先重置状态后再执行")

    try:
        result = await execute_and_record_cron_job(job_id)
        return {"status": result["status"], "result": result["result"], "error": result["error"]}
    except Exception as e:
        raise HTTPException(500, f"执行失败: {e}")


@router.get("/{job_id}/runs", response_model=CronJobRunListResponse)
async def list_cron_job_runs(
    job_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List execution logs for a cron job."""
    job = await db.get(CronJob, job_id)
    if not job:
        raise HTTPException(404, "定时任务不存在")
    if current_user.role.value != "admin" and job.user_id != current_user.id:
        raise HTTPException(403, "无权访问")

    total = (
        await db.execute(
            select(func.count()).select_from(CronJobRun).where(CronJobRun.cron_job_id == job_id)
        )
    ).scalar() or 0

    items = (
        await db.execute(
            select(CronJobRun)
            .where(CronJobRun.cron_job_id == job_id)
            .order_by(CronJobRun.started_at.desc())
            .offset((page - 1) * size)
            .limit(size)
        )
    ).scalars().all()

    return CronJobRunListResponse(
        items=[_cron_job_run_response(run) for run in items],
        total=total,
        page=page,
        size=size,
    )
