"""MCP Server registration & management API routes."""

import json
import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, false
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.skill import MCPServer
from app.schemas.mcp import (
    MCPServerCreate, MCPServerUpdate,
    MCPServerResponse, MCPServerListResponse,
)
from app.services.auth import get_current_admin, get_current_user

router = APIRouter(prefix="/api/mcp", tags=["MCP Servers"])


def _gen_id() -> str:
    return str(uuid.uuid4())


def _server_to_response(s: MCPServer) -> MCPServerResponse:
    return MCPServerResponse(
        id=s.id,
        tenant_id=s.tenant_id,
        name=s.name,
        transport_type=s.transport_type,
        endpoint=s.endpoint,
        command=s.command,
        args_json=s.args_json,
        env_json=s.env_json,
        timeout_seconds=s.timeout_seconds,
        is_active=s.is_active,
        is_builtin=s.is_builtin,
        created_at=s.created_at,
    )


# ── CRUD ──

@router.post("/servers", response_model=MCPServerResponse, status_code=201)
async def create_server(
    data: MCPServerCreate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Register a new MCP server."""
    server = MCPServer(
        id=_gen_id(),
        tenant_id=current_user.tenant_id,
        name=data.name,
        transport_type=data.transport_type,
        endpoint=data.endpoint,
        command=data.command,
        args_json=data.args_json,
        env_json=data.env_json,
        timeout_seconds=data.timeout_seconds,
        is_active=data.is_active,
        created_at=datetime.utcnow(),
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    return _server_to_response(server)


@router.get("/servers", response_model=MCPServerListResponse)
async def list_servers(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    is_active: bool | None = Query(None),
    include_builtin: bool = Query(False, description="Include platform built-in servers (e.g. Python Executor)"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List registered MCP servers (tenant-scoped).

    Built-in servers are excluded by default so the user-facing MCP management
    UI only shows user-managed servers; pass include_builtin=true to opt in.
    """
    conditions = []
    if current_user.tenant_id:
        conditions.append(MCPServer.tenant_id == current_user.tenant_id)
    if search:
        conditions.append(MCPServer.name.ilike(f"%{search}%"))
    if is_active is not None:
        conditions.append(MCPServer.is_active == is_active)
    if not include_builtin:
        conditions.append(MCPServer.is_builtin == false())

    count_q = select(func.count()).select_from(MCPServer)
    if conditions:
        count_q = count_q.where(*conditions)
    total = (await db.execute(count_q)).scalar() or 0

    items_q = select(MCPServer).order_by(MCPServer.created_at.desc())
    if conditions:
        items_q = items_q.where(*conditions)
    items_q = items_q.offset((page - 1) * size).limit(size)
    servers = (await db.execute(items_q)).scalars().all()

    return MCPServerListResponse(
        items=[_server_to_response(s) for s in servers],
        total=total, page=page, size=size,
    )


@router.get("/servers/{server_id}", response_model=MCPServerResponse)
async def get_server(
    server_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single MCP server by ID."""
    server = await db.get(MCPServer, server_id)
    if not server:
        raise HTTPException(404, "MCP_SERVER_NOT_FOUND")
    return _server_to_response(server)


@router.patch("/servers/{server_id}", response_model=MCPServerResponse)
async def update_server(
    server_id: str,
    data: MCPServerUpdate,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an MCP server configuration."""
    server = await db.get(MCPServer, server_id)
    if not server:
        raise HTTPException(404, "MCP_SERVER_NOT_FOUND")
    # Built-in servers are managed by code/seed; block any user edit (incl. rename).
    if server.is_builtin:
        raise HTTPException(403, "MCP_SERVER_BUILTIN_NO_EDIT")

    if data.name is not None:
        server.name = data.name
    if data.transport_type is not None:
        server.transport_type = data.transport_type
    if data.endpoint is not None:
        server.endpoint = data.endpoint
    if data.command is not None:
        server.command = data.command
    if data.args_json is not None:
        server.args_json = data.args_json
    if data.env_json is not None:
        server.env_json = data.env_json
    if data.timeout_seconds is not None:
        server.timeout_seconds = data.timeout_seconds
    if data.is_active is not None:
        server.is_active = data.is_active

    await db.commit()
    await db.refresh(server)
    return _server_to_response(server)


@router.delete("/servers/{server_id}")
async def delete_server(
    server_id: str,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete an MCP server registration."""
    server = await db.get(MCPServer, server_id)
    if not server:
        raise HTTPException(404, "MCP_SERVER_NOT_FOUND")
    # Built-in servers are mandatory platform infrastructure (e.g. Python
    # Executor); deleting them would break run_python for every conversation.
    if server.is_builtin:
        raise HTTPException(403, "MCP_SERVER_BUILTIN_NO_DELETE")
    await db.delete(server)
    await db.commit()
    return {"status": "deleted"}


@router.post("/servers/refresh-tools")
async def refresh_tools(
    current_user: User = Depends(get_current_admin),
):
    """Manually refresh the in-memory tool registry from all active MCP servers."""
    from app.services.tool_registry import tool_registry
    await tool_registry.refresh()
    return {"status": "refreshed", **tool_registry.stats}


# ── Test Connection ──

@router.post("/servers/{server_id}/test")
async def test_server(
    server_id: str,
    current_user: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Test MCP server connection and list available tools."""
    server = await db.get(MCPServer, server_id)
    if not server:
        raise HTTPException(404, "MCP_SERVER_NOT_FOUND")

    if server.transport_type != "http":
        return {"ok": True, "message": "STDIO_TRANSPORT_NO_AUTO_TEST", "tools": []}

    if not server.endpoint:
        raise HTTPException(400, "MCP_SERVER_HTTP_NO_ENDPOINT")

    try:
        async with httpx.AsyncClient(timeout=min(server.timeout_seconds, 30)) as client:
            # MCP JSON-RPC: tools/list
            resp = await client.post(
                server.endpoint,
                json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
                headers={"Content-Type": "application/json"},
            )
            if resp.status_code != 200:
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}

            body = resp.json()
            if "error" in body:
                return {"ok": False, "error": json.dumps(body["error"])}

            tools = body.get("result", {}).get("tools", [])
            return {
                "ok": True,
                "message": f"MCP_CONNECT_OK_TOOLS_{len(tools)}",
                "tools": [{"name": t.get("name", "?"), "description": t.get("description", "")[:200]} for t in tools],
            }
    except httpx.ConnectError as e:
        return {"ok": False, "error": f"MCP_CONNECT_FAILED: {str(e)}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
