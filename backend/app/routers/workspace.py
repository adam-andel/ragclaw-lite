"""Per-user sandbox workspace file manager proxy.

Proxies file CRUD + download operations to the MCP REPL server's internal
``/workspace/`` API. Every request is forwarded with the trusted
``REPL_AUTH_SECRET`` (in ``X-Repl-Auth``) and the user's dedicated sandbox
UID (in ``X-Repl-Uid``), so the MCP server scopes all operations to that
user's ``user_u<uid>`` root dir under ``_allow_dir``.

This is the only way the Backend can reach those files: ``/app/workspace`` is a
tmpfs living *inside* the mcp-repl container, not on the shared ``ragclaw_data``
volume the Backend mounts — hence the proxy, exactly like the chat download
links which now also route through ``/api/workspace/download``.
"""
from __future__ import annotations

import io
import zipfile
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
    search: str = Query("", description="按文件名递归搜索（在当前路径范围内，不区分大小写）"),
):
    """List a directory inside the user's sandbox root."""
    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/"
    params = {"path": path}
    if search:
        params["search"] = search
    return await _json_proxy("GET", url, headers=_ws_headers(uid), params=params)


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


@router.post("/download-zip")
async def download_zip(
    user: User = Depends(get_current_user),
    body: dict = Body(...),
):
    """Bundle the given files/dirs into a single zip, preserving the sandbox
    directory structure. Directories are expanded recursively; each entry keeps
    its relative path from the sandbox root as its archive path."""
    import httpx
    from urllib.parse import quote

    src_paths = body.get("paths")
    if not isinstance(src_paths, list) or not src_paths:
        raise HTTPException(400, detail="缺少要下载的路径")
    # Optional top-level folder name inside the archive.
    root_dir = (str(body.get("root", "") or "")).strip("/")

    uid = await _repl_uid_or_403(user)
    base = _mcp_base()
    headers = _ws_headers(uid)
    client = httpx.AsyncClient(timeout=60.0)

    async def _list(path: str) -> list:
        """Return entries under `path` ('' = sandbox root) from the MCP server.
        We build the query string inline (single percent-encode via `quote`,
        safe='' keeps slashes) rather than passing `params=`, because httpx
        would otherwise re-encode the already-encoded value and produce a
        double-encoded path (e.g. `dir1%252Fsub`) that MCP can't resolve."""
        qpath = quote(path, safe="") if path else ""
        resp = await client.get(
            f"{base}/workspace/?path={qpath}", headers=headers
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("entries", [])

    async def _collect(path: str) -> list:
        """Recursively collect every file (type != 'dir') under a DIRECTORY."""
        out: list = []
        for e in await _list(path):
            if e.get("type") == "dir":
                out.extend(await _collect(e["rel_path"]))
            else:
                out.append(e["rel_path"])
        return out

    async def _expand(src: str):
        """Expand a selected path into (anchor, files).
        `anchor` is the parent dir of `src`; it is the prefix we strip so the
        zip keeps only the *internal* structure of the selection (a selected
        dir becomes a top-level folder, a selected file becomes a bare name).
        A file yields itself; a directory is recursed. We can't blindly `_list`
        the path (listing a FILE makes MCP return 'no such directory'), so we
        list its PARENT dir and look up its type first."""
        src = str(src).strip("/")
        parent = src.rsplit("/", 1)[0] if "/" in src else ""
        entry = next(
            (e for e in await _list(parent) if str(e.get("rel_path", "")).strip("/") == src),
            None,
        )
        if entry is None:
            raise HTTPException(404, detail=f"no such path: {src}")
        if entry.get("type") == "dir":
            files = await _collect(src)
        else:
            files = [src]
        return parent, files

    async def _gen():
        buf = io.BytesIO()
        try:
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                used: set = set()
                for src in src_paths:
                    src = str(src).strip("/")
                    anchor, files = await _expand(src)
                    prefix = (anchor + "/") if anchor else ""
                    for rel in files:
                        rel = str(rel).strip("/")
                        # Drop the anchor (parent) prefix so the archive holds
                        # only the selection's internal structure: a selected
                        # dir stays a top-level folder, a selected file a bare
                        # name.
                        arcname = rel[len(prefix):] if prefix and rel.startswith(prefix) else rel
                        if not arcname:
                            continue
                        # Avoid duplicate archive paths (e.g. overlapping dirs).
                        if arcname in used:
                            continue
                        used.add(arcname)
                        resp = await client.get(
                            f"{base}/workspace/{quote(rel, safe='')}", headers=headers
                        )
                        resp.raise_for_status()
                        data = resp.content
                        zf.writestr(arcname, data)
            yield buf.getvalue()
        except httpx.HTTPStatusError as e:
            # Surface MCP/handler errors as an error doc instead of a corrupted
            # zip. The client detects this via the magic-byte / prefix check.
            yield b"__RAGCLAW_ZIP_ERROR__" + (e.response.text or "").encode("utf-8")
        except HTTPException as e:
            yield b"__RAGCLAW_ZIP_ERROR__" + str(e.detail).encode("utf-8")
        except Exception as e:  # noqa: BLE001 - never emit a broken zip silently
            yield b"__RAGCLAW_ZIP_ERROR__" + str(e).encode("utf-8")
        finally:
            await client.aclose()

    disp_base = root_dir or "workspace"
    disp = (
        f'attachment; filename="{quote(disp_base + ".zip", safe="")}";'
        f" filename*=UTF-8''{quote(disp_base + ".zip", safe='')}"
    )
    return StreamingResponse(
        _gen(),
        media_type="application/zip",
        headers={"Content-Disposition": disp},
    )
