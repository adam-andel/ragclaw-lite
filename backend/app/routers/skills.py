"""Folder-based Skill CRUD, upload, resource management, and sync API routes."""

import io
import json
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File, Form
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.models.skill import Skill
from app.schemas.skill import (
    SkillCreate, SkillUpdate, SkillResponse,
    SkillListResponse,
    ResourceListResponse, ResourceFileInfo, ResourceUploadResponse,
    SyncResponse,
)
from app.services.auth import get_current_staff, get_current_user
from app.services.skill_manager import (
    get_skill_dir, get_skill_md_path, read_skill_md, parse_skill_md,
    create_skill_folder, update_skill_md, delete_skill_folder, replace_skill_folder,
    list_resource_files, save_resource_file, delete_resource_file,
    scan_skills_dir, sanitize_folder_name, build_skill_md, sync_skills_to_db,
    get_skill_by_id,
)
from app.services.skill_script_loader import clear_cache as clear_script_cache

router = APIRouter(prefix="/api/skills", tags=["Skills"])


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _skill_to_response(skill: Skill, include_content: bool = False) -> SkillResponse:
    """Build SkillResponse from DB row, optionally reading SKILL.md content."""
    mcp_servers = []
    skill_md_content = None

    content = read_skill_md(skill.folder_name)
    if content:
        skill_md_content = content if include_content else None
        parsed = parse_skill_md(content)
        mcp_servers = parsed.get("mcp_servers", [])

    return SkillResponse(
        id=skill.id,
        tenant_id=skill.tenant_id,
        folder_name=skill.folder_name,
        name=skill.name,
        description=skill.description,
        is_active=skill.is_active,
        created_at=skill.created_at,
        updated_at=skill.updated_at,
        mcp_servers=mcp_servers,
        skill_md_content=skill_md_content,
    )


# ── CRUD ──

@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    data: SkillCreate,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Create a new skill online — generates SKILL.md + folder + DB index."""
    folder_name = sanitize_folder_name(data.name)

    # Check folder doesn't exist
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        raise HTTPException(400, f"Skill 文件夹 '{folder_name}' 已存在")

    # Create folder + SKILL.md
    create_skill_folder(
        folder_name=folder_name,
        name=data.name,
        description=data.description,
        mcp_servers=data.mcp_servers,
        body=data.body,
    )

    # Create DB index
    skill = Skill(
        tenant_id=current_user.tenant_id,
        folder_name=folder_name,
        name=data.name,
        description=(data.description or "")[:500],
        is_active=data.is_active,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    return _skill_to_response(skill, include_content=True)


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

    items_q = select(Skill).order_by(Skill.updated_at.desc())
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
    """Get a single skill by ID, including full SKILL.md content."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")
    return _skill_to_response(skill, include_content=True)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    data: SkillUpdate,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Update SKILL.md content. Re-syncs DB index from parsed front matter."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    # Write SKILL.md
    update_skill_md(skill.folder_name, data.content)

    # Re-parse and update DB index (is_active is UI-managed, not from front matter)
    parsed = parse_skill_md(data.content)
    skill.name = parsed["name"] or skill.name
    skill.description = (parsed["description"] or "")[:500]
    skill.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(skill)

    # Clear script tool cache for this skill
    clear_script_cache(skill.folder_name)

    return _skill_to_response(skill, include_content=True)


@router.delete("/{skill_id}")
async def delete_skill(
    skill_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Delete a skill — removes folder + DB index."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    # Delete folder
    delete_skill_folder(skill.folder_name)
    clear_script_cache(skill.folder_name)

    # Delete DB index
    await db.delete(skill)
    await db.commit()
    return {"status": "deleted"}


# ── Folder Upload ──

@router.post("/upload", response_model=SkillResponse, status_code=201)
async def upload_folder(
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Upload a skill folder (multipart with webkitRelativePath).

    Each file is accompanied by a 'paths' entry containing its relative path
    (e.g. 'my-skill/SKILL.md', 'my-skill/scripts/utils.py').
    """
    if not files or not paths or len(files) != len(paths):
        raise HTTPException(400, "文件和路径不匹配")

    # Extract top-level folder name from first path
    first_path = paths[0]
    parts = first_path.replace("\\", "/").split("/", 1)
    if len(parts) < 2:
        raise HTTPException(400, "路径格式错误，应包含顶层文件夹名")

    raw_folder_name = parts[0]
    folder_name = sanitize_folder_name(raw_folder_name)

    # Check folder doesn't exist
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        raise HTTPException(400, f"Skill 文件夹 '{folder_name}' 已存在")

    # Collect files: {relative_path: content}
    file_map = {}
    has_skill_md = False
    for upload_file, rel_path in zip(files, paths):
        # Strip top-level folder name from path
        path_parts = rel_path.replace("\\", "/").split("/", 1)
        if len(path_parts) < 2:
            continue  # Skip the top-level folder itself
        rel = path_parts[1]

        if rel.upper() == "SKILL.MD":
            has_skill_md = True

        content = await upload_file.read()
        file_map[rel] = content

    if not has_skill_md:
        raise HTTPException(400, "上传的文件夹必须包含 SKILL.md")

    # Write files to disk
    skill_dir.mkdir(parents=True, exist_ok=True)
    for rel_path, content in file_map.items():
        target = (skill_dir / rel_path).resolve()
        # Security: prevent path traversal
        if not str(target).startswith(str(skill_dir.resolve())):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    # Parse SKILL.md and create DB index
    skill_md_content = read_skill_md(folder_name)
    parsed = parse_skill_md(skill_md_content)

    skill = Skill(
        tenant_id=current_user.tenant_id,
        folder_name=folder_name,
        name=parsed["name"] or folder_name,
        description=(parsed["description"] or "")[:500],
        is_active=True,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    return _skill_to_response(skill, include_content=True)


@router.post("/upload-zip", response_model=SkillResponse, status_code=201)
async def upload_zip(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Upload a skill as a ZIP file. Extracts and creates folder + DB index."""
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 文件")

    zip_bytes = await file.read()
    if len(zip_bytes) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(400, "ZIP 文件过大（超过 50MB）")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 ZIP 文件")

    # Security: check for path traversal
    for name in zf.namelist():
        if ".." in name or name.startswith("/"):
            raise HTTPException(400, f"ZIP 包含非法路径: {name}")

    # Determine top-level folder name
    names = zf.namelist()
    top_dirs = set()
    for name in names:
        parts = name.split("/", 1)
        if parts[0]:
            top_dirs.add(parts[0])

    if len(top_dirs) != 1:
        raise HTTPException(400, "ZIP 应包含单个顶层文件夹")

    raw_folder_name = top_dirs.pop()
    folder_name = sanitize_folder_name(raw_folder_name)

    # Check folder doesn't exist
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        raise HTTPException(400, f"Skill 文件夹 '{folder_name}' 已存在")

    # Extract
    skill_dir.mkdir(parents=True, exist_ok=True)
    has_skill_md = False
    for name in names:
        if name.endswith("/"):
            continue
        parts = name.split("/", 1)
        if len(parts) < 2:
            continue
        rel = parts[1]
        if rel.upper() == "SKILL.MD":
            has_skill_md = True

        target = (skill_dir / rel).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(zf.read(name))

    if not has_skill_md:
        delete_skill_folder(folder_name)
        raise HTTPException(400, "ZIP 包必须包含 SKILL.md")

    # Parse SKILL.md and create DB index
    skill_md_content = read_skill_md(folder_name)
    parsed = parse_skill_md(skill_md_content)

    skill = Skill(
        tenant_id=current_user.tenant_id,
        folder_name=folder_name,
        name=parsed["name"] or folder_name,
        description=(parsed["description"] or "")[:500],
        is_active=True,
    )
    db.add(skill)
    await db.commit()
    await db.refresh(skill)

    return _skill_to_response(skill, include_content=True)


# ── Re-upload (replace entire folder) ──

@router.post("/{skill_id}/reupload", response_model=SkillResponse)
async def reupload_folder(
    skill_id: str,
    files: list[UploadFile] = File(...),
    paths: list[str] = Form(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Re-upload a skill folder, replacing all existing files. Preserves DB is_active."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    if not files or not paths or len(files) != len(paths):
        raise HTTPException(400, "文件和路径不匹配")

    # Extract top-level folder name from first path and validate it matches
    first_path = paths[0]
    parts = first_path.replace("\\", "/").split("/", 1)
    if len(parts) < 2:
        raise HTTPException(400, "路径格式错误，应包含顶层文件夹名")

    raw_folder_name = parts[0]
    folder_name = sanitize_folder_name(raw_folder_name)
    if folder_name != skill.folder_name:
        raise HTTPException(
            400,
            f"上传的顶层文件夹名 '{folder_name}' 与技能的文件夹名 '{skill.folder_name}' 不一致"
        )

    file_map: dict[str, bytes] = {}
    has_skill_md = False
    for upload_file, rel_path in zip(files, paths):
        path_parts = rel_path.replace("\\", "/").split("/", 1)
        if len(path_parts) < 2:
            continue
        rel = path_parts[1]
        if rel.upper() == "SKILL.MD":
            has_skill_md = True
        content = await upload_file.read()
        file_map[rel] = content

    if not has_skill_md:
        raise HTTPException(400, "上传的文件夹必须包含 SKILL.md")

    replace_skill_folder(folder_name, file_map)
    clear_script_cache(skill.folder_name)

    # Update DB index from new SKILL.md (keep is_active unchanged)
    skill_md_content = read_skill_md(folder_name)
    parsed = parse_skill_md(skill_md_content)
    skill.name = parsed["name"] or skill.name
    skill.description = (parsed["description"] or "")[:500]
    skill.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(skill)

    return _skill_to_response(skill, include_content=True)


@router.post("/{skill_id}/reupload-zip", response_model=SkillResponse)
async def reupload_zip(
    skill_id: str,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Re-upload a skill as a ZIP file, replacing all existing files. Preserves DB is_active."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(400, "请上传 .zip 文件")

    zip_bytes = await file.read()
    if len(zip_bytes) > 50 * 1024 * 1024:  # 50MB limit
        raise HTTPException(400, "ZIP 文件过大（超过 50MB）")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile:
        raise HTTPException(400, "无效的 ZIP 文件")

    for name in zf.namelist():
        if ".." in name or name.startswith("/"):
            raise HTTPException(400, f"ZIP 包含非法路径: {name}")

    names = zf.namelist()
    top_dirs = set()
    for name in names:
        parts = name.split("/", 1)
        if parts[0]:
            top_dirs.add(parts[0])

    if len(top_dirs) != 1:
        raise HTTPException(400, "ZIP 应包含单个顶层文件夹")

    raw_folder_name = top_dirs.pop()
    folder_name = sanitize_folder_name(raw_folder_name)
    if folder_name != skill.folder_name:
        raise HTTPException(
            400,
            f"ZIP 顶层文件夹名 '{folder_name}' 与技能的文件夹名 '{skill.folder_name}' 不一致"
        )

    file_map: dict[str, bytes] = {}
    has_skill_md = False
    for name in names:
        if name.endswith("/"):
            continue
        parts = name.split("/", 1)
        if len(parts) < 2:
            continue
        rel = parts[1]
        if rel.upper() == "SKILL.MD":
            has_skill_md = True
        file_map[rel] = zf.read(name)

    if not has_skill_md:
        raise HTTPException(400, "ZIP 包必须包含 SKILL.md")

    replace_skill_folder(folder_name, file_map)
    clear_script_cache(skill.folder_name)

    skill_md_content = read_skill_md(folder_name)
    parsed = parse_skill_md(skill_md_content)
    skill.name = parsed["name"] or skill.name
    skill.description = (parsed["description"] or "")[:500]
    skill.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(skill)

    return _skill_to_response(skill, include_content=True)


# ── Toggle active ──

@router.patch("/{skill_id}/toggle", response_model=SkillResponse)
async def toggle_skill(
    skill_id: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Toggle the UI-managed is_active flag in DB. Does not modify SKILL.md."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    skill.is_active = not skill.is_active
    skill.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(skill)
    return _skill_to_response(skill, include_content=True)


# ── Resource Management ──

@router.get("/{skill_id}/resources", response_model=ResourceListResponse)
async def list_resources(
    skill_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """List all resource files in a skill folder."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    files = list_resource_files(skill.folder_name)
    return ResourceListResponse(
        scripts=[ResourceFileInfo(**f) for f in files.get("scripts", [])],
        data=[ResourceFileInfo(**f) for f in files.get("data", [])],
        references=[ResourceFileInfo(**f) for f in files.get("references", [])],
        _root=[ResourceFileInfo(**f) for f in files.get("_root", [])],
    )


@router.post("/{skill_id}/resources", response_model=ResourceUploadResponse, status_code=201)
async def upload_resource(
    skill_id: str,
    subdir: str = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Upload a resource file to a skill's subdirectory (scripts/data/references)."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    content = await file.read()
    try:
        path = save_resource_file(skill.folder_name, subdir, file.filename or "unnamed", content)
    except ValueError as e:
        raise HTTPException(400, str(e))

    # Clear script cache if uploading to scripts/
    if subdir == "scripts":
        clear_script_cache(skill.folder_name)

    return ResourceUploadResponse(path=path, size=len(content))


@router.delete("/{skill_id}/resources/{subdir}/{filename:path}")
async def delete_resource(
    skill_id: str,
    subdir: str,
    filename: str,
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Delete a resource file from a skill's subdirectory."""
    skill = await get_skill_by_id(db, skill_id)
    if not skill:
        raise HTTPException(404, "技能不存在")

    try:
        delete_resource_file(skill.folder_name, subdir, filename)
    except ValueError as e:
        raise HTTPException(400, str(e))

    if subdir == "scripts":
        clear_script_cache(skill.folder_name)

    return {"status": "deleted"}


# ── Sync ──

@router.post("/sync", response_model=SyncResponse)
async def sync_skills(
    current_user: User = Depends(get_current_staff),
    db: AsyncSession = Depends(get_db),
):
    """Sync filesystem skills to DB index.

    - Adds DB rows for folders not in DB
    - Updates name/description/is_active from SKILL.md
    - Deactivates DB rows if folder is missing
    """
    result = await sync_skills_to_db(db)
    return SyncResponse(**result)
