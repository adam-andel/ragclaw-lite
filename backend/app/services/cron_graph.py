"""Cron job persistence and execution helpers.

Creation is handled directly by the chat agent via the create_cron meta tool
(backend/app/services/agent_nodes.py -> _execute_create_cron), which calls
_make_create_tool to persist a CronJob. This module keeps the write-time tool
(_CreateCronJobTool / _make_create_tool) and the execution subgraph
(run_cron_execution_subgraph).
"""

import json
import logging
import re
from datetime import datetime, timezone

import httpx
import pytz

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

from app.database import async_session
from app.models.cron_job import CronJob, CronJobStatus
from app.services.config_manager import config_manager
from app.services.i18n import t
from app.services.cron_parser import compute_next_run
from app.services.llm_client import llm_client

logger = logging.getLogger("ragclaw.cron")


# ── Tool schemas ──


class _CreateCronJobInput(BaseModel):
    name: str = Field(description="Short name of the scheduled task")
    cron_expr: str = Field(description="Linux crontab 5-field expression")
    task_content: str = Field(description="The exact task to execute")
    max_runs: int | None = Field(default=None, description="Maximum number of executions; omit for infinite")
    description: str = Field(default="", description="Optional longer description")


class _RecordCronResultInput(BaseModel):
    cron_id: str = Field(description="ID of the cron job")
    cron_result: str = Field(description="Concise summary of the execution result")


# ── Tools ──


class _CreateCronJobTool(BaseTool):
    """Persist a cron job created from natural language."""

    name: str = "create_cron_job"
    description: str = (
        "Create a scheduled cron job. Use when the user wants a recurring or "
        "one-time task executed at specific times. The job will be persisted "
        "in the database and triggered by the scheduler."
    )
    args_schema: type[BaseModel] = _CreateCronJobInput

    def _run(self, *args, **kwargs) -> str:
        raise NotImplementedError("Use _arun")

    async def _arun(self, **kwargs) -> str:
        name = kwargs["name"]
        cron_expr = kwargs["cron_expr"]
        task_content = kwargs["task_content"]
        max_runs = kwargs.get("max_runs")
        description = kwargs.get("description", "")

        user_id = self.metadata.get("user_id") if self.metadata else None
        tenant_id = self.metadata.get("tenant_id") if self.metadata else None
        kb_id = self.metadata.get("kb_id") if self.metadata else None
        skill_id = self.metadata.get("skill_id") if self.metadata else None
        workspace_dir = self.metadata.get("workspace_dir") if self.metadata else None
        # Interpret the cron expression in the user's local timezone so the
        # schedule matches the wall-clock time they spoke ("15:55" => 15:55 local,
        # not 15:55 UTC). pytz validates the zone inside compute_next_run and
        # falls back to UTC on an unknown name.
        tz_name = (self.metadata.get("timezone") if self.metadata else None) or "UTC"

        async with async_session() as db:
            job = CronJob(
                tenant_id=tenant_id,
                user_id=user_id,
                name=name,
                description=description or None,
                cron_expr=cron_expr,
                timezone=tz_name,
                max_runs=max_runs if max_runs else None,
                task_content=task_content,
                kb_id=kb_id,
                skill_id=skill_id,
                workspace_dir=workspace_dir,
                status=CronJobStatus.SCHEDULED,
                next_run_at=compute_next_run(cron_expr, tz_name),
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

            next_run_display = None
            if job.next_run_at:
                try:
                    local_tz = pytz.timezone(tz_name)
                    local_dt = job.next_run_at.replace(tzinfo=timezone.utc).astimezone(local_tz)
                    next_run_display = local_dt.strftime("%Y-%m-%d %H:%M")
                except pytz.UnknownTimeZoneError:
                    next_run_display = job.next_run_at.isoformat()

            result = {
                "cron_id": job.id,
                "name": job.name,
                "cron_expr": job.cron_expr,
                "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                "next_run_at_display": next_run_display,
                "timezone": tz_name,
                "message": t(
                    "cron_created_confirm",
                    config_manager.prompt_language,
                    name=job.name,
                ),
            }
            return json.dumps(result, ensure_ascii=False)


class _RecordCronResultTool(BaseTool):
    """Record the execution result of a finished cron job."""

    name: str = "record_cron_result"
    description: str = (
        "Record the result of a cron job execution into the database. "
        "Call this tool after finishing the scheduled task so the system can "
        "persist the outcome and display it to the user."
    )
    args_schema: type[BaseModel] = _RecordCronResultInput

    def _run(self, *args, **kwargs) -> str:
        raise NotImplementedError("Use _arun")

    async def _arun(self, **kwargs) -> str:
        cron_id = kwargs["cron_id"]
        cron_result = kwargs["cron_result"]

        async with async_session() as db:
            job = await db.get(CronJob, cron_id)
            if not job:
                return json.dumps({"error": f"Cron job {cron_id} not found"}, ensure_ascii=False)

            job.last_result = cron_result[:2000]
            job.last_run_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
            return json.dumps({"status": "recorded", "cron_id": cron_id}, ensure_ascii=False)


def _make_create_tool(user_id: str | None, tenant_id: str | None, kb_id: str | None, skill_id: str | None, timezone: str | None = None, workspace_dir: str | None = None) -> BaseTool:
    tool = _CreateCronJobTool()
    tool.metadata = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "skill_id": skill_id,
        "timezone": timezone,
        "workspace_dir": workspace_dir,
    }
    return tool


def _make_record_tool() -> BaseTool:
    return _RecordCronResultTool()


# ── Text-tool-call parsing (shared by execution text fallback) ──


_TOOL_CALL_RE = re.compile(
    r"\{\s*[\"']tool[\"']\s*:\s*[\"']([^\"']+)[\"']\s*,\s*[\"']arguments[\"']\s*:\s*(\{.*?\})\s*\}",
    re.DOTALL,
)


def _parse_text_tool_call(text: str, tools: list[BaseTool]) -> dict | None:
    """Parse a JSON tool call from model text output."""
    match = _TOOL_CALL_RE.search(text)
    if not match:
        return None
    tool_name = match.group(1)
    raw_args = match.group(2)
    try:
        args = json.loads(raw_args)
    except json.JSONDecodeError:
        return None

    # Validate that the requested tool exists.
    if not any(t.name == tool_name for t in tools):
        return None

    return {
        "id": "manual_tool_call",
        "type": "tool_call",
        "name": tool_name,
        "args": args,
    }


# ── Helpers ──


def _tool_to_dict(tool: BaseTool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object", "properties": {}},
        },
    }


# ── Text-based fallback for providers without tool calling ──


async def _record_cron_result_text_fallback(job: CronJob) -> str:
    """Hand-written tool loop for recording a cron job result."""
    tool = _make_record_tool()

    invocation_prompt = (
        "You are an autonomous task executor. Complete the task described by the user. "
        "After you finish, call the record_cron_result tool by outputting ONLY a single JSON object like:\n"
        '{"tool": "record_cron_result", "arguments": {"cron_id": "...", "cron_result": "..."}}\n\n'
        f"Available tool schema:\n{json.dumps(_tool_to_dict(tool), ensure_ascii=False, indent=2)}\n\n"
        "Do not wrap the JSON in markdown fences. If the task generated a document, include the link in cron_result."
    )

    response = await llm_client.chat(
        messages=[{"role": "system", "content": invocation_prompt}, {"role": "user", "content": job.task_content}],
        temperature=0.3,
        max_tokens=config_manager.max_tokens,
    )

    tool_call = _parse_text_tool_call(response, [tool])
    if tool_call:
        await tool.ainvoke(tool_call["args"])

    return response


# ── Public runners ──


async def run_cron_execution_subgraph(job: CronJob) -> str:
    """Execute a cron job by replaying the scene it was created in.

    The job stores the knowledge base (kb_id), skill (skill_id) and working
    directory (workspace_dir) the user had selected in the chat. We feed those
    into the very same agent graph used by the live chat, so execution behaves
    exactly as if the user had run the task in that conversation. The cron-rule
    is disabled for the final summary so the task is never re-scheduled.
    """
    task = job.task_content or ""
    if not task.strip():
        return "(任务内容为空，未执行)"

    # Imported lazily to avoid a circular import (agent_graph imports this module).
    from app.services.agent_graph import ragclaw_agent_graph, sandbox_network_rule
    from app.services.config_manager import config_manager
    from app.services.i18n import t as _t

    # The cron rule (which carries these two constraints at creation time) is
    # deliberately disabled during execution so the task is never re-scheduled.
    # But the executing agent is the one that actually WRITES and RUNS the code,
    # so the same constraints must be restated here — otherwise a run that cannot
    # reach the network improvises placeholder data and still reports success.
    task_query = (
        task
        + "\n\n---\n"
        + sandbox_network_rule(execution=True)
        + "\n\n"
        + _t("cron_no_fallback_rule", config_manager.prompt_language)
    )

    initial_state = {
        "query": task_query,
        "available_tools": [],
        "active_skill": None,
        "skill_stack": [],
        "loaded_skill_ids": [],
        "skill_switch_count": 0,
        "tool_messages": [],
        "tool_results": [],
        "download_entries": [],
        "context": "",
        "tool_round": 0,
        # Max agent tool-decision rounds (configurable, default 20; applies to all runs).
        "tool_round_quota": config_manager.agent_round_quota,
        # Max skill switches (configurable, default 10; applies to all runs).
        "skill_switch_quota": config_manager.skill_switch_quota,
        "route": None,
        "cache_hit": None,
        "final_answer": None,
        "kb_id": job.kb_id or "",
        "skill_id": job.skill_id,
        "subdir": job.workspace_dir or "",
        "emit": None,
        "user_id": job.user_id,
        "tenant_id": job.tenant_id,
        "conversation_history": [],
        "conversation_id": job.id,
        # Never re-detect / re-create a cron job while executing one.
        "skip_cache": True,
    }

    try:
        state = await ragclaw_agent_graph.run(initial_state)
    except Exception as e:
        logger.exception("Cron execution agent run failed: %s", e)
        return f"(执行出错: {e})"

    # Produce the final natural-language answer the same way the live chat does,
    # but without the cron-rule (the task must not be turned into a new cron job).
    try:
        messages, _ = ragclaw_agent_graph.build_generation_messages(state, include_cron_rule=False)
        answer = ""
        async for chunk in llm_client.chat_stream(messages):
            if isinstance(chunk, dict):
                if chunk.get("type") == "usage":
                    continue
                answer += chunk.get("content", "")
            else:
                answer += chunk
        answer = answer.strip()
        if answer:
            return answer
    except Exception as e:
        logger.exception("Cron execution final generation failed: %s", e)

    if state.get("final_answer"):
        return state["final_answer"]
    return "(任务已执行，但无文本结果)"
