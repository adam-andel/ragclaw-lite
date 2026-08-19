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
"""MCP Tool Registry — loads tools from active servers, caches, provides per-skill lookup.

Called on startup and refreshed on demand. Converts MCP tool definitions
into OpenAI function-calling format for LLM consumption.

In the folder-based Skill architecture, MCP tools are declared in SKILL.md
front matter (mcp_servers: [server_name]). This registry resolves those
names to MCPServer records and fetches their tools.
"""

import logging

from sqlalchemy import select

from app.database import async_session
from app.models.skill import MCPServer
from app.services.mcp_client import mcp_client, ToolDef

logger = logging.getLogger("ragclaw.tool_registry")


def _tool_to_openai(t: ToolDef, mcp_server_id: str | None = None) -> dict:
    """Convert a ToolDef to OpenAI function-calling format.

    If mcp_server_id is provided, adds _mcp_server_id metadata for executor routing.
    """
    tool = {
        "type": "function",
        "function": {
            "name": t.name,
            "description": t.description or t.name,
            "parameters": t.parameters or {"type": "object", "properties": {}},
        },
    }
    if mcp_server_id:
        tool["_source"] = "mcp"
        tool["_mcp_server_id"] = mcp_server_id
    return tool


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

    async def get_mcp_tools(self, mcp_server_names: list[str]) -> list[dict]:
        """Get tools from MCP servers declared by name in SKILL.md front matter.

        Args:
            mcp_server_names: List of MCP server names (e.g. ["Python executor"])

        Returns:
            List of tool dicts in OpenAI format with _mcp_server_id metadata.
        """
        if not mcp_server_names:
            return []

        # Trigger refresh if not initialized yet
        if not self._initialized:
            logger.info("ToolRegistry: not initialized yet, triggering lazy refresh")
            try:
                await self.refresh()
            except Exception as e:
                logger.warning("ToolRegistry: lazy refresh failed: %s", e)
                return []

        # Find MCPServer records by name
        async with async_session() as db:
            result = await db.execute(
                select(MCPServer).where(MCPServer.name.in_(mcp_server_names))
            )
            servers = result.scalars().all()

        if not servers:
            logger.warning("ToolRegistry: no MCP servers found for names=%s", mcp_server_names)
            return []

        tools: list[dict] = []
        for server in servers:
            if not server.is_active:
                continue

            server_id = server.id
            server_tools = self._tools.get(server_id)

            # Lazy refresh if not in cache
            if server_tools is None:
                logger.info("ToolRegistry: server %s not in cache, lazy-refreshing...", server.name)
                try:
                    cfg = {"id": server.id, "transport_type": server.transport_type,
                           "endpoint": server.endpoint, "command": server.command,
                           "args_json": server.args_json, "env_json": server.env_json,
                           "timeout_seconds": server.timeout_seconds}
                    new_tools = await mcp_client.list_tools(cfg)
                    self._tools[server_id] = new_tools
                    self._server_healthy[server_id] = True
                    server_tools = new_tools
                    logger.info("ToolRegistry: lazy-refreshed %s (%d tools)", server.name, len(new_tools))
                except Exception as ex:
                    logger.warning("ToolRegistry: lazy-refresh failed for %s: %s", server.name, ex)
                    self._server_healthy[server_id] = False
                    continue

            for t in server_tools:
                tools.append(_tool_to_openai(t, mcp_server_id=server_id))

        logger.info("ToolRegistry: get_mcp_tools(names=%s) → %d tools", mcp_server_names, len(tools))
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
