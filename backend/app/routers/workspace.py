"""Per-user sandbox workspace file manager proxy.

Proxies file CRUD + download operations to the MCP REPL server's internal
``/workspace/`` API. Every request is forwarded with the trusted
``REPL_AUTH_SECRET`` (in ``X-Repl-Auth``) and the user's dedicated sandbox
UID (in ``X-Repl-Uid``), so the MCP server scopes all operations to that
user's ``user_u<uid>`` root dir under ``_allow_dir``.

This is the only way the Backend can reach those files: ``/app/workspace`` is a
tmpfs living *inside* the mcp-repl container, not on the shared ``erag_data``
volume the Backend mounts — hence the proxy, exactly like ``/api/download``.
"""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import settings
from app.models.user import User
from app.services.auth import get_current_user
from app.services.repl_auth import get_user_repl_uid

router = APIRouter(prefix="/api/workspace", tags=["workspace"])


async def _repl_uid_or_403(user: User) -> int:
    """Resolve the user's sandbox UID, or 503 if the sandbox is uninitialised."""
    uid = await get_user_repl_uid(user.id)
    if uid is None:
        raise HTTPException(503, detail="用户沙箱未初始化")
    return uid


def _ws_headers(uid: int) -> dict:
    """Trusted internal headers the MCP server expects for /workspace."""
    from app.services.config_manager import config_manager

    secret = config_manager.repl_auth_secret
    if not secret:
        raise HTTPException(503, detail="REPL 认证未配置")
    return {"X-Repl-Auth": secret, "X-Repl-Uid": str(uid)}


def _mcp_base() -> str:
    return settings.mcp_repl_internal_url.rstrip("/")


async def _json_proxy(method: str, url: str, *, headers: dict,
                      json_body=None, params=None) -> JSONResponse:
    """Forward a request to the MCP server and echo its JSON + status code."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method, url, headers=headers, json=json_body, params=params
            )
    except httpx.ConnectError:
        raise HTTPException(503, detail="MCP REPL 服务不可用")
    except Exception as e:  # noqa: BLE001 - surface proxy failures uniformly
        raise HTTPException(502, detail=f"工作空间代理错误: {e}")

    try:
        payload = resp.json()
    except Exception:
        payload = {"detail": resp.text}
    return JSONResponse(content=payload, status_code=resp.status_code)


@router.get("/list")
async def list_dir(
    user: User = Depends(get_current_user),
    path: str = Query("", description="相对路径（在用户沙箱根目录内）"),
):
    """List a directory inside the user's sandbox root."""
    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/"
    return await _json_proxy("GET", url, headers=_ws_headers(uid), params={"path": path})


@router.post("")
async def create_or_update(
    user: User = Depends(get_current_user),
    body: dict = Body(...),
):
    """Create/update: mkdir | upload | rename (action-driven)."""
    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/"
    return await _json_proxy("POST", url, headers=_ws_headers(uid), json_body=body)


@router.delete("")
async def delete_path(
    user: User = Depends(get_current_user),
    path: str = Query(..., description="要删除的文件或目录的相对路径"),
):
    """Delete a file or directory (recursive) inside the user's sandbox root."""
    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/{path.lstrip('/')}"
    return await _json_proxy("DELETE", url, headers=_ws_headers(uid))


@router.get("/download")
async def download(
    user: User = Depends(get_current_user),
    path: str = Query(..., description="要下载的文件的相对路径"),
):
    """Stream a file from the user's sandbox root back to the client."""
    import httpx

    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/{path.lstrip('/')}"
    # Open the upstream connection OUTSIDE any `async with` that would close
    # it before StreamingResponse consumes the generator. The connection must
    # stay alive until _gen finishes streaming, then is closed in `finally`.
    client = httpx.AsyncClient(timeout=60.0)
    try:
        resp = await client.send(
            httpx.Request("GET", url, headers=_ws_headers(uid)), stream=True
        )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(503, detail="MCP REPL 服务不可用")
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        raise HTTPException(502, detail=f"下载代理错误: {e}")

    if resp.status_code == 404:
        await client.aclose()
        raise HTTPException(404, detail="文件不存在")
    if resp.status_code != 200:
        await client.aclose()
        raise HTTPException(502, detail=f"MCP 错误 {resp.status_code}")

    async def _gen():
        try:
            async for chunk in resp.aiter_bytes():
                yield chunk
        finally:
            await resp.aclose()
            await client.aclose()

    # RFC 5987: percent-encode the filename so non-ASCII names (Chinese,
    # spaces, ...) don't break the ASCII-only HTTP header. Starlette
    # serialises headers as latin-1, so a raw Chinese name raises
    # UnicodeEncodeError and surfaces as a 502. Browsers use the filename*
    # slot to restore the real name.
    from urllib.parse import quote

    fname = path.rstrip("/").split("/")[-1] or "download"
    disp = (
        f'attachment; filename="{quote(fname, safe="")}";'
        f" filename*=UTF-8''{quote(fname, safe='')}"
    )
    return StreamingResponse(
        _gen(),
        media_type="application/octet-stream",
        headers={"Content-Disposition": disp},
    )
