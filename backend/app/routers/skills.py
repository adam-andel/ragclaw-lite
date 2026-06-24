"""Skill CRUD & tool-binding API routes."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.user import User
from app.models.skill import Skill, SkillTool, MCPServer
from app.schemas.skill import (
    SkillCreate, SkillUpdate, SkillResponse, SkillToolInfo,
    SkillToolBindRequest, SkillToolBindResponse, SkillListResponse,
)
from app.services.auth import get_current_staff, get_current_user

router = APIRouter(prefix="/api/skills", tags=["Skills"])


def _gen_id() -> str:
    return str(uuid.uuid4())


def _skill_to_response(skill: Skill) -> SkillResponse:
    tool_infos = []
    for st in skill.tools or []:
        tool_infos.append(SkillToolInfo(
            id=st.id,
            tool_name=st.tool_name,
            mcp_server_id=st.mcp_server_id,
            mcp_server_name=getattr(st.mcp_server, "name", "") if st.mcp_server else "",
        ))
    return SkillResponse(
        id=skill.id,
        tenant_id=skill.tenant_id,
        name=skill.name,
        description=skill.description,
        system_prompt=skill.system_prompt,
        is_active=skill.is_active,
        created_by=skill.created_by,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        tools=tool_infos,
    )


# ── CRUD ──

@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    data: SkillCreate,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new skill."""
    skill = Skill(
        id=_gen_id(),
        tenant_id=current_user.tenant_id,
        name=data.name,
        description=data.description,
        system_prompt=data.system_prompt,
        is_active=data.is_active,
        created_by=current_user.id,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)
    return _skill_to_response(skill)


@router.get("", response_model=SkillListResponse)
async def list_skills(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    search: str | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List skills (tenant-scoped)."""
    conditions = []
    if current_user.tenant_id:
        conditions.append(Skill.tenant_id == current_user.tenant_id)
    if search:
        conditions.append(
            (Skill.name.ilike(f"%{search}%")) | (Skill.description.ilike(f"%{search}%"))
        )

    count_q = select(func.count()).select_from(Skill)
    if conditions:
        count_q = count_q.where(*conditions)
    total = (await db.execute(count_q)).scalar() or 0

    items_q = select(Skill).options(selectinload(Skill.tools).selectinload(SkillTool.mcp_server)).order_by(Skill.updated_at.desc())
    if conditions:
        items_q = items_q.where(*conditions)
    items_q = items_q.offset((page - 1) * size).limit(size)
    skills = (await db.execute(items_q)).scalars().all()

    return SkillListResponse(
        items=[_skill_to_response(s) for s in skills],
        total=total, page=page, size=size,
    )


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Get a single skill by ID."""
    result = await db.execute(
        select(Skill).options(selectinload(Skill.tools).selectinload(SkillTool.mcp_server)).where(Skill.id == skill_id)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "技能不存在")
    return _skill_to_response(skill)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    data: SkillUpdate,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Update a skill."""
    result = await db.execute(
        select(Skill).options(selectinload(Skill.tools).selectinload(SkillTool.mcp_server)).where(Skill.id == skill_id)
    )
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "技能不存在")

    if data.name is not None:
        skill.name = data.name
    if data.description is not None:
        skill.description = data.description
    if data.system_prompt is not None:
        skill.system_prompt = data.system_prompt
    if data.is_active is not None:
        skill.is_active = data.is_active
    skill.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(skill)
    return _skill_to_response(skill)


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Delete a skill."""
    result = await db.execute(select(Skill).where(Skill.id == skill_id))
    skill = result.scalar_one_or_none()
    if not skill:
        raise HTTPException(404, "技能不存在")
    await db.delete(skill)
    await db.commit()
    return {"status": "deleted"}


# ── Tool Bindings ──

@router.post("/{skill_id}/tools", response_model=SkillToolBindResponse, status_code=201)
async def bind_tool(
    skill_id: str,
    data: SkillToolBindRequest,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Bind an MCP tool to a skill."""
    # Verify skill exists
    skill = await db.get(Skill, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    # Verify MCP server exists
    server = await db.get(MCPServer, data.mcp_server_id)
    if not server:
        raise HTTPException(404, "MCP 服务不存在")

    # Check duplicate
    existing = await db.execute(
        select(SkillTool).where(
            (SkillTool.skill_id == skill_id)
            & (SkillTool.tool_name == data.tool_name)
            & (SkillTool.mcp_server_id == data.mcp_server_id)
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(400, "该工具已绑定到此技能")

    st = SkillTool(
        id=_gen_id(),
        skill_id=skill_id,
        tool_name=data.tool_name,
        mcp_server_id=data.mcp_server_id,
        config_json=data.config_json,
    )
    db.add(st)
    await db.commit()
    await db.refresh(st)

    return SkillToolBindResponse(
        id=st.id,
        skill_id=st.skill_id,
        tool_name=st.tool_name,
        mcp_server_id=st.mcp_server_id,
    )


@router.delete("/{skill_id}/tools/{tool_id}")
async def unbind_tool(
    skill_id: str,
    tool_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Remove a tool binding from a skill."""
    result = await db.execute(
        select(SkillTool).where(
            (SkillTool.id == tool_id) & (SkillTool.skill_id == skill_id)
        )
    )
    st = result.scalar_one_or_none()
    if not st:
        raise HTTPException(404, "工具绑定不存在")
    await db.delete(st)
    await db.commit()
    return {"status": "unbound"}
