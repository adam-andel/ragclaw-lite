"""LangGraph subgraph for cron job creation and execution using ToolNode."""

import json
import logging
import re
from datetime import datetime, timezone
from typing import TypedDict, Annotated

import httpx

from langchain_core.messages import (
    BaseMessage,
    SystemMessage,
    HumanMessage,
    AIMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

from app.database import async_session
from app.models.cron_job import CronJob, CronJobStatus
from app.models.user import User
from app.services.config_manager import config_manager
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

        async with async_session() as db:
            job = CronJob(
                tenant_id=tenant_id,
                user_id=user_id,
                name=name,
                description=description or None,
                cron_expr=cron_expr,
                timezone="UTC",
                max_runs=max_runs if max_runs else None,
                task_content=task_content,
                kb_id=kb_id,
                skill_id=skill_id,
                status=CronJobStatus.SCHEDULED,
                next_run_at=compute_next_run(cron_expr, "UTC"),
            )
            db.add(job)
            await db.commit()
            await db.refresh(job)

            result = {
                "cron_id": job.id,
                "name": job.name,
                "cron_expr": job.cron_expr,
                "next_run_at": job.next_run_at.isoformat() if job.next_run_at else None,
                "message": f"已创建定时任务「{job.name}」，可在定时任务管理页查看。",
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


def _make_create_tool(user_id: str | None, tenant_id: str | None, kb_id: str | None, skill_id: str | None) -> BaseTool:
    tool = _CreateCronJobTool()
    tool.metadata = {
        "user_id": user_id,
        "tenant_id": tenant_id,
        "kb_id": kb_id,
        "skill_id": skill_id,
    }
    return tool


def _make_record_tool() -> BaseTool:
    return _RecordCronResultTool()


# ── State ──


class CronGraphState(TypedDict):
    messages: Annotated[list[BaseMessage], add_messages]


# ── Agent node using existing llm_client ──


async def _agent_node(state: CronGraphState, tools: list[BaseTool]) -> dict:
    """Call LLM with tool support and append the response as a message.

    If the provider returns a 400 indicating tool calling is unsupported,
    fall back to a prompt-based JSON tool call.
    """
    messages = state["messages"]
    dict_messages = [_message_to_dict(m) for m in messages]
    tool_dicts = [_tool_to_dict(t) for t in tools]

    try:
        response = await llm_client.chat_with_tools(
            messages=dict_messages,
            tools=tool_dicts,
            temperature=0.3,
            max_tokens=config_manager.max_tokens,
        )
        ai_message = AIMessage(
            content=response.get("content", ""),
            tool_calls=[_convert_tool_call(tc) for tc in response.get("tool_calls", [])] if response.get("tool_calls") else [],
        )
        return {"messages": [ai_message]}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code != 400:
            raise
        logger.warning("LLM provider returned 400 for tool calling; falling back to prompt-based tool invocation")
        return await _agent_node_prompt_based(state, tools)


async def _agent_node_prompt_based(state: CronGraphState, tools: list[BaseTool]) -> dict:
    """Fallback agent node for providers that do not support native tool calling.

    Tool schemas are injected into the system prompt only when no tool result
    has been observed yet. After a tool runs, the model is asked to produce a
    natural-language final answer instead of another JSON tool call.
    """
    messages = list(state["messages"])
    has_tool_result = any(isinstance(m, ToolMessage) for m in messages)

    if has_tool_result:
        # Summarize step: do not ask for another tool call.
        follow_up = (
            "\n\nThe tool has already been executed and its result is in the "
            "conversation above. Respond to the user in Chinese with a friendly, "
            "concise summary. Do not output any JSON tool call."
        )
        modified_messages: list[BaseMessage] = []
        system_injected = False
        for msg in messages:
            if isinstance(msg, SystemMessage) and not system_injected:
                modified_messages.append(SystemMessage(content=msg.content + follow_up))
                system_injected = True
            else:
                modified_messages.append(msg)
        if not system_injected:
            modified_messages.insert(0, SystemMessage(content=follow_up))
    else:
        # Tool invocation step: ask for a JSON tool call.
        tool_instructions = _build_tool_prompt(tools)
        modified_messages = []
        system_injected = False
        for msg in messages:
            if isinstance(msg, SystemMessage) and not system_injected:
                modified_messages.append(SystemMessage(content=msg.content + "\n\n" + tool_instructions))
                system_injected = True
            else:
                modified_messages.append(msg)
        if not system_injected:
            modified_messages.insert(0, SystemMessage(content=tool_instructions))

    dict_messages = [_message_to_dict(m) for m in modified_messages]
    response_text = await llm_client.chat(
        messages=dict_messages,
        temperature=0.3,
        max_tokens=config_manager.max_tokens,
    )

    # Try to extract a JSON tool call from the response only when needed.
    if not has_tool_result:
        tool_call = _parse_text_tool_call(response_text, tools)
        if tool_call:
            return {"messages": [AIMessage(content=response_text, tool_calls=[tool_call])]}

    return {"messages": [AIMessage(content=response_text)]}


def _build_tool_prompt(tools: list[BaseTool]) -> str:
    """Build a prompt fragment describing available tools in JSON format."""
    schemas = []
    for tool in tools:
        schemas.append({
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object", "properties": {}},
        })
    return (
        "You have access to the following tools. "
        "When you need to use a tool, output ONLY a single JSON object in this exact format (no markdown fences):\n"
        '{"tool": "<tool_name>", "arguments": {<parameters>}}\n\n'
        "Available tools:\n" + json.dumps(schemas, ensure_ascii=False, indent=2)
    )


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


def _message_to_dict(message: BaseMessage) -> dict:
    if isinstance(message, SystemMessage):
        return {"role": "system", "content": message.content}
    if isinstance(message, HumanMessage):
        return {"role": "user", "content": message.content}
    if isinstance(message, AIMessage):
        return {"role": "assistant", "content": message.content}
    if isinstance(message, ToolMessage):
        return {"role": "tool", "content": message.content, "tool_call_id": message.tool_call_id}
    return {"role": "user", "content": str(message.content)}


def _tool_to_dict(tool: BaseTool) -> dict:
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": tool.description,
            "parameters": tool.args_schema.model_json_schema() if tool.args_schema else {"type": "object", "properties": {}},
        },
    }


def _convert_tool_call(tool_call: dict) -> dict:
    """Convert OpenAI-format tool call to LangChain internal format."""
    raw_args = tool_call["function"]["arguments"]
    args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
    return {
        "id": tool_call.get("id", ""),
        "type": "tool_call",
        "name": tool_call["function"]["name"],
        "args": args,
    }


# ── Graph builders ──


def _build_cron_subgraph(tools: list[BaseTool]):
    """Build a LangGraph subgraph with ToolNode for cron operations."""

    async def agent(state: CronGraphState) -> dict:
        return await _agent_node(state, tools)

    tool_node = ToolNode(tools)

    graph = StateGraph(CronGraphState)
    graph.add_node("agent", agent)
    graph.add_node("tools", tool_node)
    graph.set_entry_point("agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")

    return graph.compile()


# ── Provider compatibility ──


def _is_native_tool_calling_supported() -> bool:
    """Return True when the configured LLM provider supports OpenAI-style tool calling.

    Known unsupported providers fall back to a prompt-based JSON tool invocation.
    """
    base_url = (config_manager.base_url or "").lower()
    provider = getattr(config_manager, "llm_provider", "").lower()
    unsupported_markers = ("tencentmaas", "tencent", "hunyuan")
    return not any(m in base_url or m in provider for m in unsupported_markers)


# ── Text-based fallback for providers without tool calling ──


async def _create_cron_job_text_fallback(
    payload: dict,
    user_id: str | None,
    tenant_id: str | None,
    kb_id: str | None,
    skill_id: str | None,
) -> str:
    """Hand-written tool loop for providers that reject the tools parameter."""
    tool = _make_create_tool(user_id, tenant_id, kb_id, skill_id)

    invocation_prompt = (
        "You are a task scheduling assistant. The user wants to create a scheduled task.\n\n"
        "Call the create_cron_job tool by outputting ONLY a single JSON object like:\n"
        '{"tool": "create_cron_job", "arguments": {"name": "...", "cron_expr": "...", "task_content": "...", "max_runs": null, "description": ""}}\n\n'
        f"Available tool schema:\n{json.dumps(_tool_to_dict(tool), ensure_ascii=False, indent=2)}\n\n"
        "Do not wrap the JSON in markdown fences."
    )

    user_prompt = (
        f"Create a cron job with name '{payload['name']}', cron expression '{payload['cron_expr']}', "
        f"task content: {payload['task_content']}."
    )
    if payload.get("description"):
        user_prompt += f" Description: {payload['description']}."
    if payload.get("max_runs"):
        user_prompt += f" Max runs: {payload['max_runs']}."

    response = await llm_client.chat(
        messages=[{"role": "system", "content": invocation_prompt}, {"role": "user", "content": user_prompt}],
        temperature=0.3,
        max_tokens=config_manager.max_tokens,
    )

    tool_call = _parse_text_tool_call(response, [tool])
    if not tool_call:
        # The model failed to emit a tool call; return its raw text as a best-effort response.
        return response

    tool_result = await tool.ainvoke(tool_call["args"])

    # Build confirmation from the tool result to avoid a second LLM call on
    # providers that reject certain message shapes.
    try:
        result_data = json.loads(tool_result)
        next_run = result_data.get("next_run_at", "unknown")
        return (
            f"已创建定时任务「{result_data.get('name', '')}」，"
            f"下次执行时间：{next_run}，"
            f"可在定时任务管理页查看。"
        )
    except json.JSONDecodeError:
        return "定时任务已创建。"


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


async def run_cron_creation_subgraph(
    payload: dict,
    user_id: str | None,
    tenant_id: str | None,
    kb_id: str | None,
    skill_id: str | None,
) -> str:
    """Run the cron creation subgraph and return a user-friendly confirmation."""
    if not _is_native_tool_calling_supported():
        return await _create_cron_job_text_fallback(payload, user_id, tenant_id, kb_id, skill_id)

    tools = [_make_create_tool(user_id, tenant_id, kb_id, skill_id)]
    graph = _build_cron_subgraph(tools)

    system_prompt = (
        "You are a task scheduling assistant. The user wants to create a scheduled task. "
        "Use the create_cron_job tool to persist the job. "
        "After the tool returns the cron_id, respond to the user in Chinese with a friendly confirmation, "
        "including the task name, cron expression, and next run time."
    )

    user_prompt = (
        f"Create a cron job with name '{payload['name']}', cron expression '{payload['cron_expr']}', "
        f"task content: {payload['task_content']}."
    )
    if payload.get("description"):
        user_prompt += f" Description: {payload['description']}."
    if payload.get("max_runs"):
        user_prompt += f" Max runs: {payload['max_runs']}."

    initial_state: CronGraphState = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ],
    }

    final_state = await graph.ainvoke(initial_state)
    final_message = final_state["messages"][-1]
    return str(final_message.content)


async def run_cron_execution_subgraph(job: CronJob) -> str:
    """Run the cron execution subgraph and record the result via tool."""
    if not _is_native_tool_calling_supported():
        return await _record_cron_result_text_fallback(job)

    tools = [_make_record_tool()]
    graph = _build_cron_subgraph(tools)

    tenant_id = None
    if job.user_id:
        async with async_session() as db:
            user = await db.get(User, job.user_id)
            if user:
                tenant_id = user.tenant_id

    system_prompt = (
        "You are an autonomous task executor. Complete the task described by the user. "
        "After you finish, call the record_cron_result tool with a concise summary of the result. "
        "If the task generated a document, include the download link in the summary."
    )

    user_prompt = job.task_content

    initial_state: CronGraphState = {
        "messages": [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ],
    }

    final_state = await graph.ainvoke(initial_state)
    final_message = final_state["messages"][-1]
    return str(final_message.content)
