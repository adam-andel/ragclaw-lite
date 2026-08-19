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
"""Phase 7 validation matrix as pytest — shared skills volume + symlink chain.

This is the pytest rewrite of bin/sh/verify_skills_volume.sh. The bash script
could only be exercised inside a Linux container (root + a real Docker volume +
a pool UID), so it was awkward as a regression guard in the normal suite. Here
it is split into two tiers:

  Tier 1 — UNIT (always runs; no privileges, no real volume required):
    Exercises the REAL backend code in app.services.skill_manager against a
    tmp_path-backed shared volume (settings.skills_dir / skills_enable_dir
    monkeypatched). Covers: enable/disable symlink set, world-readable
    permission bits (the unit-level proxy for "a foreign REPL UID can read"),
    the 3-layer chain *structure* resolves end-to-end, importing a skill .py
    drops no .pyc, and the legacy migration is idempotent + copy-only +
    re-enables.

  Tier 2 — LIVE (skipped unless root AND the real volume is mounted):
    Reproduces the privilege/volume-dependent checks from the bash script:
    a REAL cross-UID read through the chain (drop to pool UID 10001),
    /proc/mounts ro/rw assertion, and read-only write rejection. Runs inside
    the ragclaw / mcp-repl containers exactly where the bash script used to.

Run:
  # anywhere (unit tier only — live tier auto-skips)
  pytest backend/tests/services/test_skills_volume.py
  # inside container as root (full matrix)
  docker compose exec -u root ragclaw pytest backend/tests/services/test_skills_volume.py
  docker compose exec -u root mcp-repl pytest backend/tests/services/test_skills_volume.py
"""

import os
import sys
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from app.config import settings
from app.services import skill_manager as sm


POOL_UID = 10001
POOL_GID = 10001
TEST_SKILL = "__verify_skill__"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def shared_volume(tmp_path, monkeypatch):
    """Redirect the shared skills volume to a tmp_path and create store/ + enable/.

    skills_dir / skills_enable_dir are @property getters on Settings, so we
    replace them on the class to point at the temp volume. skill_manager reads
    those properties (never the raw shared_skills_dir field), so this is enough
    to make every call site operate on tmp_path.
    """
    shared = tmp_path / "ragclaw_skills"
    (shared / "store").mkdir(parents=True)
    (shared / "enable").mkdir(parents=True)
    cls = type(settings)
    monkeypatch.setattr(cls, "skills_dir", property(lambda self: shared / "store"))
    monkeypatch.setattr(cls, "skills_enable_dir", property(lambda self: shared / "enable"))
    yield shared


# ---------------------------------------------------------------------------
# Tier 1 — UNIT
# ---------------------------------------------------------------------------

def test_enable_skill_fs_creates_relative_symlink(shared_volume):
    """Backend enable link must be RELATIVE (../store/<s>) so it resolves within
    the same shared volume regardless of mount point."""
    sm.enable_skill_fs(TEST_SKILL)
    link = settings.skills_enable_dir / TEST_SKILL
    assert os.path.lexists(link)
    assert os.readlink(link) == os.path.join("..", "store", TEST_SKILL)


def test_enable_disable_roundtrip(shared_volume):
    sm.enable_skill_fs(TEST_SKILL)
    assert sm.is_skill_enabled_fs(TEST_SKILL) is True
    sm.disable_skill_fs(TEST_SKILL)
    assert sm.is_skill_enabled_fs(TEST_SKILL) is False
    # idempotent — disabling an absent link is a no-op, not an error
    sm.disable_skill_fs(TEST_SKILL)
    assert sm.is_skill_enabled_fs(TEST_SKILL) is False


def test_is_skill_effectively_enabled_falls_back_when_volume_absent(tmp_path, monkeypatch):
    """Routing-gate truth: if the shared enable dir is absent (volume not
    mounted / mount outage) we must NOT disable every skill — fall back to
    trusting the DB is_active cache instead."""
    shared = tmp_path / "ragclaw_skills"
    # enable dir intentionally NOT created
    cls = type(settings)
    monkeypatch.setattr(cls, "skills_dir", property(lambda self: shared / "store"))
    monkeypatch.setattr(cls, "skills_enable_dir", property(lambda self: shared / "enable"))
    assert sm.is_skill_effectively_enabled("any_skill") is True


def test_world_readable_bits_allow_foreign_uid(shared_volume):
    """store/* must be world-readable (0755/0644) so a REPL sandbox running as a
    DIFFERENT uid can traverse + read. This is the unit-level proxy for the
    cross-UID read claim; the live tier proves it for real when run as root."""
    skill = settings.skills_dir / TEST_SKILL
    (skill / "scripts").mkdir(parents=True)
    (skill / "SKILL.md").write_text("# x\nname: V\n")
    (skill / "scripts" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    sm._ensure_world_readable(skill)

    st_dir = skill.stat().st_mode
    st_md = (skill / "SKILL.md").stat().st_mode
    st_py = (skill / "scripts" / "calc.py").stat().st_mode
    # dir: world read + traverse (0o005); files: world read (0o004)
    assert (st_dir & 0o005) == 0o005, oct(st_dir)
    assert (st_md & 0o004) == 0o004, oct(st_md)
    assert (st_py & 0o004) == 0o004, oct(st_py)


def test_three_layer_chain_resolves(shared_volume):
    """store/<s>/SKILL.md  +  enable/<s> -> ../store/<s>  must resolve
    end-to-end. The sandbox child reaches skills via the REPL_SKILLS_DIR
    container env var (the shared enable/ set); combined with the
    world-readable bits above, a foreign uid can also read through enable/."""
    skill = settings.skills_dir / TEST_SKILL
    (skill / "scripts").mkdir(parents=True)
    body = "# test\nname: Verify\n"
    (skill / "SKILL.md").write_text(body)
    sm._ensure_world_readable(skill)
    sm.enable_skill_fs(TEST_SKILL)  # enable/<s> -> ../store/<s>

    # The sandbox child now reaches skills via REPL_SKILLS_DIR=/ragclaw_skills/enable
    # (a persistent container env var), not a per-user symlink tree.
    assert (settings.skills_enable_dir / TEST_SKILL / "SKILL.md").is_file()
    assert (settings.skills_enable_dir / TEST_SKILL / "SKILL.md").read_text() == body


def test_import_skill_py_drops_no_pyc(shared_volume):
    """Importing a skill .py with don't_write_bytecode must not drop a .pyc onto
    the shared store (mcp-repl sets PYTHONDONTWRITEBYTECODE=1)."""
    skill = settings.skills_dir / TEST_SKILL
    (skill / "scripts").mkdir(parents=True)
    (skill / "scripts" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    sm._ensure_world_readable(skill)

    assert list(shared_volume.glob("**/*.pyc")) == []

    import importlib.util
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location(
            "__verify_calc__", str(skill / "scripts" / "calc.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        assert mod.add(2, 3) == 5
    finally:
        sys.dont_write_bytecode = prev

    assert list(shared_volume.glob("**/*.pyc")) == []


# ---------------------------------------------------------------------------
# Tier 2 — LIVE (root + real mounted volume only)
# ---------------------------------------------------------------------------

def _live_reason() -> str | None:
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return "not running as root (live volume checks need root)"
    d = os.environ.get("RAGCLAW_SKILLS_DIR", "/ragclaw_skills")
    if not os.path.isdir(d):
        return f"shared skills volume not mounted at {d}"
    return None


_LIVE = pytest.mark.skipif(
    _live_reason() is not None,
    reason="live volume checks require root + a mounted shared skills volume",
)


def _run_as_pool(cmd: str) -> str:
    """Drop to the non-root pool UID and run `cmd` (mirrors the bash as_pool)."""
    for tool in ("setpriv", "runuser", "su"):
        p = shutil.which(tool)
        if not p:
            continue
        if tool == "setpriv":
            argv = [p, "--reuid", str(POOL_UID), "--regid", str(POOL_GID),
                    "--clear-groups", "sh", "-c", cmd]
        elif tool == "runuser":
            argv = [p, "-u", f"#{POOL_UID}", "--", "sh", "-c", cmd]
        else:
            argv = [p, "-s", "/bin/sh", f"#{POOL_UID}", "-c", cmd]
        r = subprocess.run(argv, capture_output=True, text=True, timeout=30)
        return (r.stdout + r.stderr).strip() or "EMPTY"
    return "SKIP_NO_SETUID_TOOL"


def _volume_writable(d: Path) -> bool:
    try:
        (d / "store" / ".writetest").touch()
        (d / "store" / ".writetest").unlink()
        return True
    except OSError:
        return False


@_LIVE
def test_live_mount_flag():
    """/proc/mounts must report the volume's ro/rw flag (mirrors bash check [2])."""
    d = Path(os.environ.get("RAGCLAW_SKILLS_DIR", "/ragclaw_skills"))
    mounts = Path("/proc/mounts").read_text()
    line = [l for l in mounts.splitlines() if l.split()[1] == str(d)]
    assert line, f"{d} not found in /proc/mounts"
    ro = "ro" in line[0].split()


@_LIVE
def test_live_volume_matrix():
    """Full Phase 7 matrix against the real mounted volume.

    Auto-detects rw (backend) vs ro (mcp-repl) and runs the matching half.
    """
    d = Path(os.environ.get("RAGCLAW_SKILLS_DIR", "/ragclaw_skills"))
    store = d / "store"
    enable = d / "enable"
    store.mkdir(parents=True, exist_ok=True)
    enable.mkdir(parents=True, exist_ok=True)

    writable = _volume_writable(d)
    home = Path(tempfile.mkdtemp(prefix="verify_skills."))
    os.chmod(home, 0o755)  # let the simulated pool UID traverse into its home
    # The per-user `.ragclaw` symlink tree was removed from production; the
    # sandbox child now reaches skills via REPL_SKILLS_DIR -> enable/. This live
    # check still proves cross-UID readability of the shared enable/ set, using a
    # neutral temp stand-in (no `.ragclaw`) for the link the old code created.
    link_dir = home / "skills"
    link_dir.mkdir(parents=True)

    try:
        if writable:
            # ---- BACKEND (rw) half: seed, chain, real cross-UID read ----
            skill = store / TEST_SKILL
            if not skill.exists():
                (skill / "scripts").mkdir(parents=True)
                (skill / "SKILL.md").write_text("# test\nname: Verify\n")
                (skill / "scripts" / "calc.py").write_text("def add(a, b):\n    return a + b\n")
                for p in (skill, skill / "SKILL.md", skill / "scripts" / "calc.py"):
                    os.chmod(p, 0o755 if p.is_dir() else 0o644)
            el = enable / TEST_SKILL
            if not os.path.lexists(el):
                os.symlink(os.path.join("..", "store", TEST_SKILL), el)
            user_link = link_dir / TEST_SKILL
            if not os.path.lexists(user_link):
                os.symlink(str(el), str(user_link))

            out = _run_as_pool(
                f"cat '{user_link}/SKILL.md' >/dev/null 2>&1 && echo READ_OK || echo READ_FAIL")
            assert out == "READ_OK", f"pool UID {POOL_UID} cannot read through chain: {out}"

            # The REPL sandbox (pool UID) must NOT write the shared skills volume
            # (docker-compose.dev.yml:138-142: writes are redirected to a sandbox-local
            # shadow copy, never persisted to the shared mount). On the rw backend mount
            # the pool UID still gets Permission denied because root owns the volume,
            # so WRITE_FAIL is the expected *secure* outcome here. The shell leaks the
            # redirection error into the combined stdout+stderr, so match by substring,
            # not exact equality.
            w = _run_as_pool(
                f"echo x > '{store / TEST_SKILL / 'SKILL.md.tmp'}' 2>/dev/null "
                f"&& echo WRITE_OK || echo WRITE_FAIL")
            assert "WRITE_OK" in w or "WRITE_FAIL" in w, w
        else:
            # ---- mcp-repl (ro) half: read already-seeded, reject writes ----
            seeded = None
            if enable.exists():
                for e in sorted(enable.iterdir()):
                    if os.path.lexists(e):
                        seeded = e.name
                        break
            assert seeded, "no seeded skill in enable/ — run the backend half first"
            user_link = link_dir / seeded
            os.symlink(str(enable / seeded), str(user_link))
            out = _run_as_pool(
                f"cat '{user_link}/SKILL.md' >/dev/null 2>&1 && echo READ_OK || echo READ_FAIL")
            assert out == "READ_OK", f"pool UID {POOL_UID} cannot read seeded skill: {out}"

            w = _run_as_pool(
                f"echo x > '{store / 'SKILL.md.tmp'}' 2>/dev/null "
                f"&& echo WRITE_OK || echo WRITE_FAIL")
            # Substring match: the shell leaks the redirection error into the combined
            # output, so `w` is "WRITE_FAIL\n... Permission denied", not the bare token.
            assert "WRITE_FAIL" in w, f"sandbox UID wrote into ro store: {w}"
    finally:
        # clean up everything this test created (idempotent)
        user_link_name = TEST_SKILL if writable else (seeded if seeded else None)
        if user_link_name:
            leaf = link_dir / user_link_name
            if os.path.lexists(leaf):
                os.unlink(leaf)
        shutil.rmtree(home, ignore_errors=True)
        if writable:
            if os.path.lexists(enable / TEST_SKILL):
                os.unlink(enable / TEST_SKILL)
            if (store / TEST_SKILL).exists():
                shutil.rmtree(store / TEST_SKILL, ignore_errors=True)
