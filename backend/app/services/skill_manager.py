"""Skill filesystem manager — scan/create/delete/sync folder-based skills.

The filesystem (data/skills/{folder_name}/SKILL.md) is the source of truth.
The DB skills table is a cache for fast routing.

SKILL.md format:
    ---
    name: Skill显示名
    description: "≤250字符的描述"
    mcp_servers:
      - MCP服务器名1
    is_active: true
    ---

    # Markdown body (Gotchas / Examples / Constraints etc.)
"""

import re
import shutil
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill


# ─── Path helpers ───

def get_skill_dir(folder_name: str) -> Path:
    """Return the absolute path to a skill's folder."""
    return settings.skills_dir / folder_name


def get_skill_md_path(folder_name: str) -> Path:
    """Return the absolute path to a skill's SKILL.md."""
    return get_skill_dir(folder_name) / "SKILL.md"


# ─── Folder name sanitization ───

_FOLDER_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$')


def sanitize_folder_name(name: str) -> str:
    """Convert a display name to a valid folder name.

    Rules: lowercase, spaces→hyphens, keep only [a-z0-9_-], must start with
    alphanumeric. Returns sanitized name or raises ValueError.
    """
    # Replace spaces and common separators with hyphens
    sanitized = re.sub(r'[\s/\\]+', '-', name.strip().lower())
    # Remove invalid characters
    sanitized = re.sub(r'[^a-z0-9_\-]', '', sanitized)
    # Collapse multiple hyphens
    sanitized = re.sub(r'-+', '-', sanitized).strip('-_')
    if not sanitized:
        raise ValueError(f"无法从名称 '{name}' 生成合法的文件夹名")
    if not _FOLDER_NAME_RE.match(sanitized):
        raise ValueError(f"文件夹名 '{sanitized}' 不合法（仅允许字母数字、下划线、连字符）")
    return sanitized


# ─── SKILL.md parsing / writing ───

def parse_skill_md(content: str) -> dict:
    """Parse SKILL.md content into front_matter dict + body string.

    Returns {"name": ..., "description": ..., "mcp_servers": [...],
             "is_active": bool, "body": "markdown body"}.
    """
    front_matter = {}
    body = content

    # Extract YAML front matter between --- delimiters
    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                front_matter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                front_matter = {}
            body = parts[2].strip()

    return {
        "name": front_matter.get("name", ""),
        "description": front_matter.get("description", ""),
        "mcp_servers": front_matter.get("mcp_servers", []) or [],
        "is_active": front_matter.get("is_active", True),
        "body": body,
    }


def build_skill_md(
    name: str,
    description: str,
    mcp_servers: list[str] | None = None,
    is_active: bool = True,
    body: str = "",
) -> str:
    """Build SKILL.md content from components."""
    mcp_servers = mcp_servers or []
    # Build YAML front matter
    fm_lines = ["---", f"name: {name}"]
    # Quote description if it contains special chars
    desc = description or ""
    if any(c in desc for c in [':', '#', '"', "'", '\n', '{', '}', '[', ']', ',']):
        desc = desc.replace('"', '\\"')
        fm_lines.append(f'description: "{desc}"')
    else:
        fm_lines.append(f"description: {desc}")

    if mcp_servers:
        fm_lines.append("mcp_servers:")
        for s in mcp_servers:
            fm_lines.append(f"  - {s}")
    else:
        fm_lines.append("mcp_servers: []")

    fm_lines.append(f"is_active: {'true' if is_active else 'false'}")
    fm_lines.append("---")

    header = "\n".join(fm_lines)
    if body:
        return f"{header}\n\n{body}"
    return f"{header}\n\n# {name}\n"


# ─── Filesystem operations ───

def scan_skills_dir() -> list[dict]:
    """Scan data/skills/ and return list of skill info dicts.

    Each dict: {folder_name, name, description, mcp_servers, is_active, body}
    Only folders containing SKILL.md are included.
    """
    skills = []
    if not settings.skills_dir.exists():
        return skills

    for entry in sorted(settings.skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        try:
            content = skill_md.read_text(encoding="utf-8")
            parsed = parse_skill_md(content)
            skills.append({
                "folder_name": entry.name,
                "name": parsed["name"],
                "description": parsed["description"],
                "mcp_servers": parsed["mcp_servers"],
                "is_active": parsed["is_active"],
                "body": parsed["body"],
            })
        except Exception as e:
            print(f"[skill_manager] Error parsing {skill_md}: {e}")
    return skills


def read_skill_md(folder_name: str) -> str | None:
    """Read SKILL.md content for a skill folder. Returns None if not found."""
    path = get_skill_md_path(folder_name)
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def create_skill_folder(
    folder_name: str,
    name: str,
    description: str,
    mcp_servers: list[str] | None = None,
    is_active: bool = True,
    body: str = "",
) -> str:
    """Create a new skill folder with SKILL.md.

    Returns the folder_name. Raises ValueError if folder already exists.
    """
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        raise ValueError(f"Skill 文件夹 '{folder_name}' 已存在")

    skill_dir.mkdir(parents=True)
    content = build_skill_md(name, description, mcp_servers, is_active, body)
    get_skill_md_path(folder_name).write_text(content, encoding="utf-8")
    return folder_name


def write_skill_folder(folder_name: str, files: dict[str, str]) -> None:
    """Write multiple files into a skill folder (for upload).

    files: {relative_path: content}. Creates folder if not exists.
    Overwrites existing files.
    """
    skill_dir = get_skill_dir(folder_name)
    skill_dir.mkdir(parents=True, exist_ok=True)

    for rel_path, content in files.items():
        # Security: prevent path traversal
        target = (skill_dir / rel_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise ValueError(f"非法路径: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")


def update_skill_md(folder_name: str, content: str) -> None:
    """Overwrite SKILL.md with new content."""
    path = get_skill_md_path(folder_name)
    if not path.exists():
        raise ValueError(f"Skill '{folder_name}' 的 SKILL.md 不存在")
    path.write_text(content, encoding="utf-8")


def delete_skill_folder(folder_name: str) -> None:
    """Delete an entire skill folder."""
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        shutil.rmtree(skill_dir)


# ─── Resource file management ───

ALLOWED_SUBDIRS = {"scripts", "data", "references"}


def list_resource_files(folder_name: str) -> dict[str, list[dict]]:
    """List all resource files in a skill folder, grouped by subdirectory.

    Returns {"scripts": [{name, path, size}], "data": [...], "references": [...]}
    Also includes top-level files (excluding SKILL.md) under "_root".
    """
    skill_dir = get_skill_dir(folder_name)
    result = {d: [] for d in ALLOWED_SUBDIRS}
    result["_root"] = []

    if not skill_dir.exists():
        return result

    for entry in sorted(skill_dir.iterdir()):
        if entry.is_dir() and entry.name in ALLOWED_SUBDIRS:
            for f in sorted(entry.rglob("*")):
                if f.is_file():
                    result[entry.name].append({
                        "name": f.name,
                        "path": str(f.relative_to(skill_dir)),
                        "size": f.stat().st_size,
                    })
        elif entry.is_file() and entry.name != "SKILL.md":
            result["_root"].append({
                "name": entry.name,
                "path": entry.name,
                "size": entry.stat().st_size,
            })
    return result


def save_resource_file(folder_name: str, subdir: str, filename: str, content: bytes) -> str:
    """Save a file to a skill's subdirectory (scripts/data/references).

    Returns the relative path. Raises ValueError for invalid subdir.
    """
    if subdir not in ALLOWED_SUBDIRS:
        raise ValueError(f"非法子目录: {subdir}，允许: {ALLOWED_SUBDIRS}")

    skill_dir = get_skill_dir(folder_name)
    target_dir = skill_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / filename).resolve()

    # Security: prevent path traversal
    if not str(target).startswith(str(target_dir.resolve())):
        raise ValueError(f"非法文件路径: {filename}")

    target.write_bytes(content)
    return f"{subdir}/{filename}"


def delete_resource_file(folder_name: str, subdir: str, filename: str) -> None:
    """Delete a resource file from a skill's subdirectory."""
    if subdir not in ALLOWED_SUBDIRS:
        raise ValueError(f"非法子目录: {subdir}")

    target = (get_skill_dir(folder_name) / subdir / filename).resolve()
    target_dir = (get_skill_dir(folder_name) / subdir).resolve()

    if not str(target).startswith(str(target_dir)):
        raise ValueError(f"非法文件路径: {filename}")
    if target.exists():
        target.unlink()


# ─── DB sync ───

async def sync_skills_to_db(session: AsyncSession) -> dict:
    """Sync filesystem skills to DB index.

    - Adds DB rows for folders not in DB
    - Updates name/description/is_active from SKILL.md
    - Marks DB rows as inactive if folder is missing
    - Does NOT delete DB rows (user may have temporarily moved folders)

    Returns {"added": N, "updated": N, "deactivated": N}
    """
    fs_skills = scan_skills_dir()
    fs_folders = {s["folder_name"] for s in fs_skills}

    # Load all DB rows
    result = await session.execute(select(Skill))
    db_skills = {s.folder_name: s for s in result.scalars().all()}

    added = updated = deactivated = 0

    # Add or update
    for fs_skill in fs_skills:
        folder = fs_skill["folder_name"]
        if folder not in db_skills:
            # Add new
            new_skill = Skill(
                folder_name=folder,
                name=fs_skill["name"],
                description=(fs_skill["description"] or "")[:500],
                is_active=fs_skill["is_active"],
            )
            session.add(new_skill)
            added += 1
        else:
            # Update existing
            db_skill = db_skills[folder]
            changed = False
            if db_skill.name != fs_skill["name"]:
                db_skill.name = fs_skill["name"]
                changed = True
            new_desc = (fs_skill["description"] or "")[:500]
            if db_skill.description != new_desc:
                db_skill.description = new_desc
                changed = True
            if not db_skill.is_active and fs_skill["is_active"]:
                db_skill.is_active = True
                changed = True
            if changed:
                updated += 1

    # Deactivate DB rows whose folders are missing
    for folder, db_skill in db_skills.items():
        if folder not in fs_folders and db_skill.is_active:
            db_skill.is_active = False
            deactivated += 1

    await session.commit()
    return {"added": added, "updated": updated, "deactivated": deactivated}


async def get_all_active_skills(session: AsyncSession) -> list[Skill]:
    """Get all active skill DB rows for routing (Layer 1)."""
    result = await session.execute(
        select(Skill).where(Skill.is_active == True)  # noqa: E712
    )
    return list(result.scalars().all())


async def get_skill_by_id(session: AsyncSession, skill_id: str) -> Skill | None:
    """Get a skill DB row by id."""
    result = await session.execute(select(Skill).where(Skill.id == skill_id))
    return result.scalar_one_or_none()


async def get_skill_by_folder(session: AsyncSession, folder_name: str) -> Skill | None:
    """Get a skill DB row by folder_name."""
    result = await session.execute(select(Skill).where(Skill.folder_name == folder_name))
    return result.scalar_one_or_none()
