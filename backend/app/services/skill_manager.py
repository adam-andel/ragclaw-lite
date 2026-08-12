"""Skill filesystem manager - scan/create/delete/sync folder-based skills.

The filesystem (data/skills/{folder_name}/SKILL.md) is the source of truth for
content (name, description, mcp_servers, body). The DB skills table is a cache
for fast routing, including the UI-managed is_active flag.

SKILL.md format:
    ---
    name: Skill Display Name
    description: "<=250 chars description"
    mcp_servers:
      - MCP Server Name 1
    ---

    # Markdown body (Gotchas / Examples / Constraints etc.)
"""

import hashlib
import os
import re
import shutil
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.skill import Skill

# ---- Pinyin support ----

try:
    from pypinyin import pinyin, Style
    _PYIN_AVAILABLE = True
except ImportError:
    _PYIN_AVAILABLE = False


def _to_pinyin(text: str) -> str:
    """Convert Chinese characters to pinyin with hyphens."""
    if not _PYIN_AVAILABLE:
        return re.sub(r'[^a-zA-Z0-9]', '', text)
    result = pinyin(text, style=Style.NORMAL)
    return '-'.join(item[0] for item in result if item[0])


# ---- Path helpers ----

def get_skill_dir(folder_name: str) -> Path:
    """Return the absolute path to a skill's folder."""
    return settings.skills_dir / folder_name


def get_skill_md_path(folder_name: str) -> Path:
    """Return the absolute path to a skill's SKILL.md."""
    return get_skill_dir(folder_name) / "SKILL.md"


# ---- Shared-volume enable/disable (symlink set under skills_enable_dir) ----
#
# Source of truth for "is this skill enabled" is the presence of a symlink at
#   <shared>/enable/<folder>  ->  ../store/<folder>
# rather than a DB flag. The DB is_active column (sync_skills_to_db) is kept as
# a faithful cache of this so routing/filtering stay consistent without an
# extra FS read on every request. Disabling a skill = removing the symlink;
# enabling = re-creating it. Both are atomic and idempotent.

def get_enable_link_path(folder_name: str) -> Path:
    """Absolute path of the shared enable-symlink for a skill."""
    return settings.skills_enable_dir / folder_name


def is_skill_enabled_fs(folder_name: str) -> bool:
    """True if the skill's enable-symlink exists (FS source of truth)."""
    return os.path.lexists(get_enable_link_path(folder_name))


def is_skill_effectively_enabled(folder_name: str) -> bool:
    """Routing-gate truth for "is this skill enabled right now".

    The enable-symlink on the shared volume is the source of truth, so a skill
    disabled since the last ``sync_skills_to_db`` (which only maintains the DB
    ``is_active`` cache) is dropped immediately.

    The one exception: if the shared enable directory itself is absent, the
    shared-skills volume is not mounted (legacy mode / mount outage). We must
    NOT silently disable *every* skill based on a missing directory, so we
    fall back to trusting the caller's ``is_active`` cache in that case.
    """
    if not settings.skills_enable_dir.exists():
        return True
    return is_skill_enabled_fs(folder_name)


def enable_skill_fs(folder_name: str) -> None:
    """Create the shared enable-symlink for a skill (idempotent)."""
    link = get_enable_link_path(folder_name)
    if os.path.lexists(link):
        return
    link.parent.mkdir(parents=True, exist_ok=True)
    # Relative target resolves within the same shared volume:
    #   enable/<folder> -> ../store/<folder>
    os.symlink(os.path.join("..", "store", folder_name), link)


def disable_skill_fs(folder_name: str) -> None:
    """Remove the shared enable-symlink for a skill (no-op if absent)."""
    link = get_enable_link_path(folder_name)
    if os.path.lexists(link):
        link.unlink()


def _ensure_world_readable(path: Path) -> None:
    """Recursively chmod a skill path so the per-user REPL sandbox (which runs
    as a *different* UID than the backend) can read the shared-volume files.
    Directories -> 0o755, files -> 0o644. We only ever operate on store/*, never
    on the enable/* symlinks, so no symlink-mode weirdness.
    """
    if not path.exists():
        return
    if path.is_dir():
        for root, dirs, files in os.walk(path):
            for d in dirs:
                try:
                    os.chmod(os.path.join(root, d), 0o755)
                except OSError:
                    pass
            for f in files:
                try:
                    os.chmod(os.path.join(root, f), 0o644)
                except OSError:
                    pass
        try:
            os.chmod(path, 0o755)
        except OSError:
            pass
    else:
        try:
            os.chmod(path, 0o644)
        except OSError:
            pass


def publish_skill(folder_name: str) -> None:
    """Mark a skill enabled and make its store files world-readable.

    Use after CREATE / UPLOAD so the skill is routable and the per-user REPL
    sandbox (a different UID) can read its files.
    """
    _ensure_world_readable(get_skill_dir(folder_name))
    enable_skill_fs(folder_name)


def refresh_skill_readable(folder_name: str) -> None:
    """Re-apply world-readable chmod without touching enable state.

    Use after REUPLOAD / EDIT / resource writes: the skill's enabled state must
    be preserved, only the new file permissions need fixing for the sandbox UID.
    """
    _ensure_world_readable(get_skill_dir(folder_name))


# ---- Folder name sanitization ----

_FOLDER_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$')


def sanitize_folder_name(name: str) -> str:
    """Convert a display name to a valid folder name.

    Rules:
    1. Chinese characters -> pinyin (via pypinyin if available, else stripped)
    2. ASCII: lowercase, spaces/hyphens->hyphens, keep [a-z0-9_-]
    3. Must start with alphanumeric; if result is empty, fall back to hash prefix.

    Returns sanitized name or raises ValueError.
    """
    # Step 1: Convert Chinese to pinyin
    name_ascii = _to_pinyin(name) if re.search(r'[\u4e00-\u9fff]', name) else name

    # Step 2: Standard sanitization
    sanitized = re.sub(r'[\s/\\]+', '-', name_ascii.strip().lower())
    sanitized = re.sub(r'[^a-z0-9_\-]', '', sanitized)
    sanitized = re.sub(r'-+', '-', sanitized).strip('-_')

    # Step 3: Fallback for pure non-ASCII names when pinyin unavailable
    if not sanitized:
        name_hash = hashlib.sha256(name.encode('utf-8')).hexdigest()[:8]
        sanitized = f"skill-{name_hash}"

    if not _FOLDER_NAME_RE.match(sanitized):
        raise ValueError(f"Folder name '{sanitized}' invalid (only [a-zA-Z0-9_-] allowed)")
    return sanitized


# ---- SKILL.md parsing / writing ----

def parse_skill_md(content: str) -> dict:
    """Parse SKILL.md content into front_matter dict + body string."""
    front_matter = {}
    body = content

    if content.startswith("---"):
        parts = content.split("---", 2)
        if len(parts) >= 3:
            try:
                front_matter = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                front_matter = {}
            body = parts[2].strip()

    # Force-truncate description to 250 chars for routing (Layer 1 contract)
    desc = front_matter.get("description", "") or ""
    if isinstance(desc, str) and len(desc) > 250:
        desc = desc[:250]

    return {
        "name": front_matter.get("name", ""),
        "description": desc,
        "mcp_servers": front_matter.get("mcp_servers", []) or [],
        "body": body,
    }


def build_skill_md(
    name: str,
    description: str,
    mcp_servers: list[str] | None = None,
    body: str = "",
) -> str:
    """Build SKILL.md content from components."""
    mcp_servers = mcp_servers or []
    fm_lines = ["---", f"name: {name}"]
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

    fm_lines.append("---")

    header = "\n".join(fm_lines)
    if body:
        return f"{header}\n\n{body}"
    return f"{header}\n\n# {name}\n"



# ---- Filesystem operations ----

def scan_skills_dir() -> list[dict]:
    """Scan data/skills/ and return list of skill info dicts."""
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
                "body": parsed["body"],
                "enabled": is_skill_enabled_fs(entry.name),
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
    body: str = "",
) -> str:
    """Create a new skill folder with SKILL.md.

    Returns the folder_name. Raises ValueError if folder already exists.
    """
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        raise ValueError(f"Skill folder '{folder_name}' already exists")

    skill_dir.mkdir(parents=True)
    content = build_skill_md(name, description, mcp_servers, body)
    get_skill_md_path(folder_name).write_text(content, encoding="utf-8")
    publish_skill(folder_name)  # enable + world-readable for the sandbox UID
    return folder_name



def replace_skill_folder(folder_name: str, file_map: dict[str, bytes | str]) -> None:
    """Replace an existing skill folder with new files.

    Preserves the DB-managed is_active flag by not touching the DB row.
    Raises ValueError if folder does not exist.
    """
    skill_dir = get_skill_dir(folder_name)
    if not skill_dir.exists():
        raise ValueError(f"Skill folder '{folder_name}' does not exist")

    for entry in skill_dir.iterdir():
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    for rel_path, content in file_map.items():
        target = (skill_dir / rel_path).resolve()
        if not str(target).startswith(str(skill_dir.resolve())):
            raise ValueError(f"Invalid path: {rel_path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")

    # Re-upload replaces files but must NOT change the enabled state; only fix
    # permissions so the (different-UID) sandbox can read them.
    refresh_skill_readable(folder_name)



def update_skill_md(folder_name: str, content: str) -> None:
    """Overwrite SKILL.md with new content."""
    path = get_skill_md_path(folder_name)
    if not path.exists():
        raise ValueError(f"Skill '{folder_name}' SKILL.md not found")
    path.write_text(content, encoding="utf-8")
    refresh_skill_readable(folder_name)  # fix perms on the rewritten file


def delete_skill_folder(folder_name: str) -> None:
    """Delete an entire skill folder."""
    skill_dir = get_skill_dir(folder_name)
    if skill_dir.exists():
        shutil.rmtree(skill_dir)
    # Drop the enable-symlink too so a deleted skill can never stay routable.
    link = get_enable_link_path(folder_name)
    if os.path.lexists(link):
        link.unlink()


# ---- Resource file management ----

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
        raise ValueError(f"Invalid subdir: {subdir}, allowed: {ALLOWED_SUBDIRS}")

    skill_dir = get_skill_dir(folder_name)
    target_dir = skill_dir / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    target = (target_dir / filename).resolve()

    if not str(target).startswith(str(target_dir.resolve())):
        raise ValueError(f"Invalid file path: {filename}")

    target.write_bytes(content)
    refresh_skill_readable(folder_name)  # new file must be readable by the sandbox UID
    return f"{subdir}/{filename}"


def delete_resource_file(folder_name: str, subdir: str, filename: str) -> None:
    """Delete a resource file from a skill's subdirectory."""
    if subdir not in ALLOWED_SUBDIRS:
        raise ValueError(f"Invalid subdir: {subdir}")

    target = (get_skill_dir(folder_name) / subdir / filename).resolve()
    target_dir = (get_skill_dir(folder_name) / subdir).resolve()

    if not str(target).startswith(str(target_dir)):
        raise ValueError(f"Invalid file path: {filename}")
    if target.exists():
        target.unlink()


# ---- Layer 3: on-demand resource loading ----

_RESOURCE_SUBDIRS = ALLOWED_SUBDIRS  # scripts, data, references


def get_skill_resource(folder_name: str, resource_path: str) -> str | None:
    """Read a resource file from a skill folder (Layer 3 on-demand).

    Args:
        folder_name: Skill folder name
        resource_path: Relative path within the skill folder
                       (e.g. "references/guide.md", "data/config.json")

    Returns:
        File content as string, or None if not found / access denied.
    """
    skill_dir = get_skill_dir(folder_name)
    target = (skill_dir / resource_path).resolve()

    # Security: prevent path traversal and restrict to allowed subdirs
    if not str(target).startswith(str(skill_dir.resolve())):
        return None
    if not target.is_file():
        return None
    if target.name == "SKILL.md":
        return None  # Already loaded in Layer 2

    try:
        rel = target.relative_to(skill_dir)
    except ValueError:
        return None
    parts = str(rel).replace("\\", "/").split("/")
    if len(parts) > 1 and parts[0] not in _RESOURCE_SUBDIRS:
        return None

    try:
        return target.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None  # Binary files not supported for in-context loading


def list_resource_paths(folder_name: str) -> list[str]:
    """List all readable resource paths for a skill (for tool description).

    Returns relative paths like ["references/guide.md", "data/config.json"].
    Skips binary files and SKILL.md.
    """
    skill_dir = get_skill_dir(folder_name)
    paths = []
    if not skill_dir.exists():
        return paths

    for entry in sorted(skill_dir.iterdir()):
        if entry.is_dir() and entry.name in _RESOURCE_SUBDIRS:
            for f in sorted(entry.rglob("*")):
                if f.is_file():
                    rel = str(f.relative_to(skill_dir)).replace("\\", "/")
                    paths.append(rel)
        elif entry.is_file() and entry.name != "SKILL.md":
            paths.append(entry.name)
    return paths


# ---- DB sync ----

async def sync_skills_to_db(session: AsyncSession) -> dict:
    """Sync filesystem skills to DB index.

    - Adds DB rows for folders not in DB
    - Updates name/description from SKILL.md (is_active is UI-managed)
    - Marks DB rows as inactive if folder is missing
    - Does NOT delete DB rows

    Returns {"added": N, "updated": N, "deactivated": N}
    """
    fs_skills = scan_skills_dir()
    fs_folders = {s["folder_name"] for s in fs_skills}

    result = await session.execute(select(Skill))
    db_skills = {s.folder_name: s for s in result.scalars().all()}

    added = updated = deactivated = 0

    for fs_skill in fs_skills:
        folder = fs_skill["folder_name"]
        # Source of truth for enabled state is the enable-symlink on disk.
        fs_enabled = is_skill_enabled_fs(folder)
        if folder not in db_skills:
            new_skill = Skill(
                folder_name=folder,
                name=fs_skill["name"],
                description=(fs_skill["description"] or "")[:250],
                is_active=fs_enabled,
            )
            session.add(new_skill)
            added += 1
        else:
            db_skill = db_skills[folder]
            changed = False
            if db_skill.name != fs_skill["name"]:
                db_skill.name = fs_skill["name"]
                changed = True
            new_desc = (fs_skill["description"] or "")[:250]
            if db_skill.description != new_desc:
                db_skill.description = new_desc
                changed = True
            # Keep is_active as a faithful cache of the enable-symlink state.
            if db_skill.is_active != fs_enabled:
                db_skill.is_active = fs_enabled
                changed = True
            if changed:
                updated += 1

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


async def get_skill_by_name(
    session: AsyncSession, name: str, tenant_id: str | None = None
) -> Skill | None:
    """Get an active skill DB row by display name OR folder_name.

    Used by Route D skill orchestration (use_skill). Tenant-scoped when
    tenant_id is provided, mirroring the auto-router's scoping.
    """
    stmt = select(Skill).where(
        (Skill.is_active == True)  # noqa: E712
        & ((Skill.name == name) | (Skill.folder_name == name))
    )
    if tenant_id:
        stmt = stmt.where(Skill.tenant_id == tenant_id)
    return (await session.execute(stmt)).scalar_one_or_none()
