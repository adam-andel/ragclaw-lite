"""MCP (Model Context Protocol) client — HTTP (JSON-RPC) + stdio transport.

Supports tool listing and invocation against registered MCP servers.
All operations are async-safe, with configurable timeouts.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("erag.mcp")


@dataclass
class ToolDef:
    """A tool definition as returned by tools/list."""
    name: str
    description: str = ""
    parameters: dict = field(default_factory=dict)  # JSON Schema for inputs


@dataclass
class ToolResult:
    """Result of a single tool call."""
    tool_name: str
    ok: bool
    result: str = ""       # success output as string
    error: str = ""        # error message if ok=False


class MCPClient:
    """Async MCP client supporting HTTP (JSON-RPC 2.0) and stdio transports."""

    def __init__(self):
        self._http_client = httpx.AsyncClient(timeout=60.0)
        self._subprocesses: dict[str, asyncio.subprocess.Process] = {}

    async def close(self):
        """Clean up all connections and subprocesses."""
        await self._http_client.aclose()
        for proc in self._subprocesses.values():
            try:
                proc.terminate()
            except Exception:
                pass

    # ── Public API ──

    async def list_tools(self, server_config: dict) -> list[ToolDef]:
        """Discover available tools from an MCP server.

        Args:
            server_config: dict with keys:
                transport_type: "http" | "stdio"
                endpoint: str (for http)
                command: str, args_json: str, env_json: str (for stdio)
                timeout_seconds: int

        Returns:
            List of ToolDef objects
        """
        transport = server_config.get("transport_type", "http")
        timeout = server_config.get("timeout_seconds", 30)

        if transport == "http":
            return await self._list_tools_http(server_config, timeout)
        elif transport == "stdio":
            return await self._list_tools_stdio(server_config, timeout)
        else:
            logger.warning("Unknown MCP transport: %s", transport)
            return []

    async def call_tool(
        self, server_config: dict, tool_name: str, arguments: dict
    ) -> ToolResult:
        """Execute a tool on an MCP server.

        Args:
            server_config: Same format as list_tools
            tool_name: Name of the tool to call
            arguments: Tool arguments as dict

        Returns:
            ToolResult with ok, result, error
        """
        transport = server_config.get("transport_type", "http")
        timeout = server_config.get("timeout_seconds", 30)

        try:
            if transport == "http":
                return await self._call_tool_http(server_config, tool_name, arguments, timeout)
            elif transport == "stdio":
                return await self._call_tool_stdio(server_config, tool_name, arguments, timeout)
            else:
                return ToolResult(tool_name=tool_name, ok=False, error=f"Unknown transport: {transport}")
        except asyncio.TimeoutError:
            return ToolResult(tool_name=tool_name, ok=False, error=f"Timeout after {timeout}s")
        except Exception as e:
            return ToolResult(tool_name=tool_name, ok=False, error=str(e))

    async def health_check(self, server_config: dict) -> bool:
        """Quick check whether an MCP server is reachable."""
        try:
            tools = await self.list_tools(server_config)
            return len(tools) >= 0  # empty tool list is still "reachable"
        except Exception:
            return False

    # ── HTTP Transport ──

    async def _list_tools_http(self, config: dict, timeout: int) -> list[ToolDef]:
        endpoint = config.get("endpoint", "")
        if not endpoint:
            logger.warning("HTTP MCP server has no endpoint")
            return []

        try:
            resp = await self._http_post(endpoint, "tools/list", {}, timeout)
            tools_raw = resp.get("result", {}).get("tools", [])
            return [
                ToolDef(
                    name=t.get("name", "unknown"),
                    description=t.get("description", ""),
                    parameters=t.get("inputSchema", {}),
                )
                for t in tools_raw
            ]
        except Exception as e:
            logger.warning("MCP list_tools HTTP failed: %s", e)
            return []

    async def _call_tool_http(
        self, config: dict, tool_name: str, arguments: dict, timeout: int
    ) -> ToolResult:
        endpoint = config.get("endpoint", "")
        if not endpoint:
            return ToolResult(tool_name=tool_name, ok=False, error="No endpoint configured")

        resp = await self._http_post(endpoint, "tools/call", {
            "name": tool_name,
            "arguments": arguments,
        }, timeout)

        if "error" in resp:
            return ToolResult(
                tool_name=tool_name, ok=False,
                error=json.dumps(resp["error"], ensure_ascii=False),
            )

        result = resp.get("result", {})
        content = result.get("content", [])
        if isinstance(content, list) and len(content) > 0:
            text = content[0].get("text", json.dumps(content, ensure_ascii=False))
        else:
            text = json.dumps(result, ensure_ascii=False)

        return ToolResult(tool_name=tool_name, ok=True, result=text)

    async def _http_post(self, endpoint: str, method: str, params: dict, timeout: int) -> dict:
        """Send a JSON-RPC 2.0 request via HTTP."""
        body = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        resp = await self._http_client.post(
            endpoint,
            json=body,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()

    # ── stdio Transport (skeleton — Phase 2 implements discovery only) ──

    async def _list_tools_stdio(self, config: dict, timeout: int) -> list[ToolDef]:
        proc = await self._ensure_stdio_process(config)
        if proc is None:
            return []
        return await self._stdio_rpc(proc, "tools/list", {}, timeout)

    async def _call_tool_stdio(
        self, config: dict, tool_name: str, arguments: dict, timeout: int
    ) -> ToolResult:
        proc = await self._ensure_stdio_process(config)
        if proc is None:
            return ToolResult(tool_name=tool_name, ok=False, error="Failed to start stdio process")

        try:
            tools = await self._stdio_rpc(proc, "tools/call", {
                "name": tool_name, "arguments": arguments
            }, timeout)
            return ToolResult(tool_name=tool_name, ok=True, result=json.dumps(tools, ensure_ascii=False))
        except Exception as e:
            return ToolResult(tool_name=tool_name, ok=False, error=str(e))

    async def _ensure_stdio_process(self, config: dict):
        """Get or create a persistent stdio subprocess for an MCP server."""
        server_id = config.get("id", config.get("command", "unknown"))
        if server_id in self._subprocesses:
            proc = self._subprocesses[server_id]
            if proc.returncode is not None:  # process exited
                del self._subprocesses[server_id]
            else:
                return proc

        command = config.get("command")
        if not command:
            logger.warning("stdio MCP server has no command")
            return None

        args = json.loads(config.get("args_json", "[]")) if config.get("args_json") else []
        env = json.loads(config.get("env_json", "{}")) if config.get("env_json") else {}
        merged_env = {**__import__("os").environ, **env}

        try:
            proc = await asyncio.create_subprocess_exec(
                command, *args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=merged_env,
            )
            # Send initialize
            init_req = json.dumps({
                "jsonrpc": "2.0", "id": 0,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "erag", "version": "0.3.0"},
                },
            }) + "\n"
            proc.stdin.write(init_req.encode())
            await proc.stdin.drain()

            init_resp = await asyncio.wait_for(proc.stdout.readline(), timeout=10)
            # Send initialized notification
            notif = json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n"
            proc.stdin.write(notif.encode())
            await proc.stdin.drain()

            self._subprocesses[server_id] = proc
            return proc
        except Exception as e:
            logger.warning("stdio MCP process start failed: %s", e)
            return None

    async def _stdio_rpc(self, proc, method: str, params: dict, timeout: int) -> list[ToolDef] | dict:
        """Send a JSON-RPC request over stdio and read the response."""
        req = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}) + "\n"
        proc.stdin.write(req.encode())
        await proc.stdin.drain()

        resp_line = await asyncio.wait_for(proc.stdout.readline(), timeout=timeout)
        resp = json.loads(resp_line.decode())
        if "error" in resp:
            raise RuntimeError(json.dumps(resp["error"]))
        return resp.get("result", [])


# Singleton
mcp_client = MCPClient()
