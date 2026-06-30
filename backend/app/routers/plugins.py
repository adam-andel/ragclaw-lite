"""Parser plugin management API — admin only."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.parser_plugin import ParserPluginState
from app.models.user import User
from app.schemas.parser_plugin import (
    PluginInfo,
    PluginDisablePayload,
    PluginListResponse,
)
from app.services.auth import get_current_admin
from app.services.parser import parser_service

router = APIRouter(prefix="/api/plugins", tags=["Plugins"])


@router.get("", response_model=PluginListResponse)
async def list_plugins(
    _: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all built-in parser plugins with current enabled state."""
    result = await db.execute(select(ParserPluginState))
    disabled_map = {row.name: row for row in result.scalars()}

    items = []
    for meta in parser_service.list_plugins():
        state = disabled_map.get(meta["name"])
        items.append(PluginInfo(
            **meta,
            enabled=state is None or not state.disabled,
            disabled_by=state.disabled_by if state else None,
            disabled_at=state.disabled_at if state else None,
            reason=state.reason if state else None,
        ))
    return PluginListResponse(items=items, total=len(items))


@router.post("/{name}/enable")
async def enable_plugin(
    name: str,
    operator: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Enable a previously disabled plugin."""
    valid_names = {p["name"] for p in parser_service.list_plugins()}
    if name not in valid_names:
        raise HTTPException(404, f"插件不存在: {name}")
    result = await db.execute(
        select(ParserPluginState).where(ParserPluginState.name == name)
    )
    state = result.scalar_one_or_none()
    if state:
        await db.delete(state)
        await db.commit()
    await parser_service._refresh_disabled_cache()
    return {"name": name, "enabled": True}


@router.post("/{name}/disable")
async def disable_plugin(
    name: str,
    payload: PluginDisablePayload,
    operator: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Disable a plugin. Disabled extensions will be rejected at upload."""
    valid_names = {p["name"] for p in parser_service.list_plugins()}
    if name not in valid_names:
        raise HTTPException(404, f"插件不存在: {name}")
    result = await db.execute(
        select(ParserPluginState).where(ParserPluginState.name == name)
    )
    state = result.scalar_one_or_none()
    if state is None:
        state = ParserPluginState(
            name=name,
            disabled=True,
            disabled_by=operator.id,
            disabled_at=datetime.now(timezone.utc),
            reason=payload.reason,
        )
        db.add(state)
    else:
        state.disabled = True
        state.disabled_by = operator.id
        state.disabled_at = datetime.now(timezone.utc)
        state.reason = payload.reason
    await db.commit()
    await parser_service._refresh_disabled_cache()
    return {"name": name, "enabled": False}


@router.post("/refresh-cache")
async def refresh_cache(_: User = Depends(get_current_admin)):
    """Force-refresh the in-memory enabled cache."""
    await parser_service._refresh_disabled_cache()
    return {"ok": True}
