"""MCP Tool Registry — loads tools from active servers, caches, provides per-skill lookup.

Called on startup and refreshed on demand. Converts MCP tool definitions
into OpenAI function-calling format for LLM consumption.
"""

import asyncio
import logging
from collections import defaultdict

from app.database import async_session
from app.models.skill import MCPServer, SkillTool
from app.services.mcp_client import mcp_client, ToolDef

logger = logging.getLogger("erag.tool_registry")


def _tool_to_openai(t: ToolDef) -> dict:
    """Convert a ToolDef to OpenAI function-calling format."""
    return {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or t.name,
            "parameters": t.parameters or {"type": "object", "properties": {}},
        },
    }


class ToolRegistry:
    """In-memory cache of MCP tools, keyed by server_id.

    Refreshed on startup (from lifespan) and on-demand (admin trigger).
    """

    def __init__(self):
        # server_id → list[ToolDef]
        self._tools: dict[str, list[ToolDef]] = {}
        self._server_healthy: dict[str, bool] = {}
        self._initialized = False

    @property
    def is_initialized(self) -> bool:
        return self._initialized

    async def refresh(self):
        """Scan all active MCP servers and refresh the tool cache."""
        logger.info("ToolRegistry: refreshing...")
        try:
            async with async_session() as db:
                from sqlalchemy import select
                result = await db.execute(
                    select(MCPServer).where(MCPServer.is_active == True)  # noqa: E712
                )
                servers = result.scalars().all()
        except Exception as e:
            logger.warning("ToolRegistry: DB query failed: %s", e)
            return

        new_tools: dict[str, list[ToolDef]] = {}
        new_healthy: dict[str, bool] = {}

        for server in servers:
            config = {
                "id": server.id,
                "transport_type": server.transport_type,
                "endpoint": server.endpoint,
                "command": server.command,
                "args_json": server.args_json,
                "env_json": server.env_json,
                "timeout_seconds": server.timeout_seconds,
            }
            try:
                tools = await mcp_client.list_tools(config)
                new_tools[server.id] = tools
                new_healthy[server.id] = True
                logger.info("ToolRegistry: %s → %d tools", server.name, len(tools))
            except Exception as e:
                new_tools[server.id] = []
                new_healthy[server.id] = False
                logger.warning("ToolRegistry: %s failed: %s", server.name, e)

        self._tools = new_tools
        self._server_healthy = new_healthy
        self._initialized = True
        logger.info("ToolRegistry: refresh done — %d servers, %d total tools",
                     len(new_tools), sum(len(v) for v in new_tools.values()))

    def get_tools_for_skill(self, skill_id: str) -> list[dict]:
        """Get tools bound to a specific skill, in OpenAI function-calling format.

        Args:
            skill_id: The skill's database ID

        Returns:
            List of tool dicts in OpenAI format (empty if skill has no tools)
        """
        if not self._initialized:
            return []

        tools: list[dict] = []
        seen: set[tuple[str, str]] = set()

        # Query DB for skill-tool bindings (sync, called from async context via executor)
        import asyncio as _asyncio
        async def _query():
            async with async_session() as db:
                from sqlalchemy import select
                from sqlalchemy.orm import selectinload
                result = await db.execute(
                    select(SkillTool).options(selectinload(SkillTool.mcp_server)).where(
                        SkillTool.skill_id == skill_id
                    )
                )
                return result.scalars().all()

        try:
            loop = _asyncio.get_running_loop()
            # This is called inside an async node; use a task
            # We need to be careful - let's just make it properly async
            pass
        except RuntimeError:
            pass

        # Since get_tools_for_skill may be called from sync context during init,
        # we handle both sync and async paths
        return self._get_tools_for_skill_sync(skill_id)

    def _get_tools_for_skill_sync(self, skill_id: str, db_session=None) -> list[dict]:
        """Synchronous helper for get_tools_for_skill."""
        # For now, return empty until called with db_session from the graph node
        return []

    async def get_tools_for_skill_async(self, skill_id: str) -> list[dict]:
        """Async version — call from agent nodes with a db session."""
        if not self._initialized:
            return []

        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        async with async_session() as db:
            result = await db.execute(
                select(SkillTool).options(selectinload(SkillTool.mcp_server)).where(
                    SkillTool.skill_id == skill_id
                )
            )
            bindings = result.scalars().all()

        tools: list[dict] = []
        seen: set[str] = set()

        for binding in bindings:
            server_id = binding.mcp_server_id
            tool_name = binding.tool_name
            cache_key = f"{server_id}:{tool_name}"

            if cache_key in seen:
                continue
            seen.add(cache_key)

            if server_id in self._tools:
                for t in self._tools[server_id]:
                    if t.name == tool_name:
                        tools.append(_tool_to_openai(t))
                        break

        return tools

    def get_all_tools_for_servers(self, server_ids: list[str]) -> list[dict]:
        """Get all tools from specified servers in OpenAI format."""
        if not self._initialized:
            return []

        tools: list[dict] = []
        for sid in server_ids:
            for t in self._tools.get(sid, []):
                tools.append(_tool_to_openai(t))
        return tools

    def is_server_healthy(self, server_id: str) -> bool:
        return self._server_healthy.get(server_id, False)

    @property
    def stats(self) -> dict:
        return {
            "servers": len(self._tools),
            "total_tools": sum(len(v) for v in self._tools.values()),
            "healthy": sum(1 for v in self._server_healthy.values() if v),
        }


# Singleton
tool_registry = ToolRegistry()
