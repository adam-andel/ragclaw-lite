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
import logging
import zipfile
from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse

logger = logging.getLogger("ragclaw.workspace")

from app.config import settings
from app.models.user import User
from app.services.auth import get_current_user
from app.services.mcp_client import _repush_repl_auth_secret
from app.services.repl_auth import get_user_repl_uid

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# The MCP server returns this exact body (HTTP 403) while its in-memory
# REPL_AUTH_SECRET has not been pushed yet (e.g. right after mcp-repl
# restarted but the Backend's self-heal loop had already stopped). It is the
# HTTP /workspace equivalent of the JSON-RPC -32002 "secret not configured"
# error on the REPL tool path, and is repaired the same way: re-push the
# secret once and replay the request.
_WS_AUTH_NOT_CONFIGURED = '{"error": "auth not configured"}'


def _is_auth_not_configured(resp) -> bool:
    """True when the MCP server rejected us because its secret is missing."""
    if resp.status_code != 403:
        return False
    try:
        body = resp.text.strip()
    except Exception:
        return False
    return body == _WS_AUTH_NOT_CONFIGURED or '"auth not configured"' in body


async def _repl_uid_or_403(user: User) -> int:
    """Resolve the user's sandbox UID, or 503 if the sandbox is uninitialised."""
    uid = await get_user_repl_uid(user.id)
    if uid is None:
        raise HTTPException(503, detail="WORKSPACE_SANDBOX_NOT_INIT")
    return uid


def _ws_headers(uid: int) -> dict:
    """Trusted internal headers the MCP server expects for /workspace."""
    from app.services.config_manager import config_manager

    secret = config_manager.repl_auth_secret
    if not secret:
        raise HTTPException(503, detail="WORKSPACE_REPL_AUTH_MISSING")
    return {"X-Repl-Auth": secret, "X-Repl-Uid": str(uid)}


def _mcp_base() -> str:
    return settings.mcp_repl_internal_url.rstrip("/")


async def _json_proxy(method: str, url: str, *, headers: dict,
                      json_body=None, params=None) -> JSONResponse:
    """Forward a request to the MCP server and echo its JSON + status code.

    Self-heals the same gap the REPL tool path covers: if the MCP server
    returns 403 "auth not configured" (its in-memory secret was lost, e.g.
    after a restart), re-push the secret once and replay the request before
    giving up. Reuses the shared debounced re-push so concurrent calls collapse
    into a single PUT /auth-secret, matching mcp_client.call_tool's behavior.
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                method, url, headers=headers, json=json_body, params=params
            )
            if _is_auth_not_configured(resp):
                logger.warning(
                    "workspace_auth_not_configured method=%s url=%s — re-pushing secret and retrying",
                    method, url,
                )
                if await _repush_repl_auth_secret():
                    resp = await client.request(
                        method, url, headers=headers, json=json_body, params=params
                    )
    except httpx.ConnectError:
        raise HTTPException(503, detail="WORKSPACE_MCP_UNAVAILABLE")
    except Exception as e:  # noqa: BLE001 - surface proxy failures uniformly
        raise HTTPException(502, detail=f"WORKSPACE_PROXY_ERROR: {e}")

    try:
        payload = resp.json()
    except Exception:
        payload = {"detail": resp.text}
    return JSONResponse(content=payload, status_code=resp.status_code)


@router.get("/list")
async def list_dir(
    user: User = Depends(get_current_user),
    path: str = Query("", description="Relative path (inside the user's sandbox root)"),
    search: str = Query("", description="Recursive filename search (case-insensitive, within the current path)"),
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
    path: str = Query(..., description="Relative path of the file or directory to delete"),
):
    """Delete a file or directory (recursive) inside the user's sandbox root."""
    uid = await _repl_uid_or_403(user)
    url = f"{_mcp_base()}/workspace/{path.lstrip('/')}"
    return await _json_proxy("DELETE", url, headers=_ws_headers(uid))


@router.get("/download")
async def download(
    user: User = Depends(get_current_user),
    path: str = Query(..., description="Relative path of the file to download"),
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
        # Self-heal: a freshly restarted mcp-repl may report 403 auth not
        # configured because its in-memory secret is gone. Re-push once and
        # replay, matching the JSON / workspace proxy behavior.
        if _is_auth_not_configured(resp):
            logger.warning(
                "workspace_download_auth_not_configured path=%s — re-pushing secret and retrying",
                path,
            )
            await resp.aclose()
            if await _repush_repl_auth_secret():
                resp = await client.send(
                    httpx.Request("GET", url, headers=_ws_headers(uid)), stream=True
                )
    except httpx.ConnectError:
        await client.aclose()
        raise HTTPException(503, detail="WORKSPACE_MCP_UNAVAILABLE")
    except Exception as e:  # noqa: BLE001
        await client.aclose()
        raise HTTPException(502, detail=f"WORKSPACE_DOWNLOAD_ERROR: {e}")

    if resp.status_code == 404:
        await client.aclose()
        raise HTTPException(404, detail="WORKSPACE_FILE_NOT_FOUND")
    if resp.status_code != 200:
        await client.aclose()
        raise HTTPException(502, detail=f"WORKSPACE_MCP_STATUS: {resp.status_code}")

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
        raise HTTPException(400, detail="WORKSPACE_MISSING_PATHS")
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
        double-encoded path (e.g. `dir1%252Fsub`) that MCP can't resolve.

        Self-heals a freshly restarted mcp-repl that lost its in-memory secret
        (403 auth not configured) by re-pushing the secret once and replaying.
        """
        qpath = quote(path, safe="") if path else ""
        url = f"{base}/workspace/?path={qpath}"
        resp = await client.get(url, headers=headers)
        if _is_auth_not_configured(resp):
            logger.warning(
                "workspace_zip_list_auth_not_configured path=%s — re-pushing secret and retrying",
                path,
            )
            if await _repush_repl_auth_secret():
                resp = await client.get(url, headers=headers)
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
                        if _is_auth_not_configured(resp):
                            # Rare: the secret window closed between listing and
                            # fetching. Re-push once and replay before surfacing.
                            if await _repush_repl_auth_secret():
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
