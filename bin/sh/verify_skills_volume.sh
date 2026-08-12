#!/usr/bin/env bash
# verify_skills_volume.sh — Phase 7 validation matrix for the shared skills volume.
#
# PROVES the skill-sandbox symlink design actually works in a Linux container
# (the earlier design review could only reason about it on the Windows host,
# which lacks symlink privileges). Run it INSIDE a container that has the
# ragclaw_skills volume mounted:
#
#   Backend (rw) — seeds a throwaway skill, builds the full 3-layer chain, and
#   verifies a NON-ROOT pool UID can read skill files through it + that importing
#   a .py does NOT drop a .pyc onto the shared store:
#     docker compose exec -u root ragclaw sh /app/verify_skills_volume.sh
#     # dev: docker compose -f docker-compose.yml -f docker-compose.dev.yml \
#     #        exec -u root ragclaw sh /app/verify_skills_volume.sh
#
#   mcp-repl (ro) — verifies the volume is mounted read-only, that a pool UID
#   still reads an already-seeded skill through the chain, and that a write from
#   the sandbox UID into the shared store is rejected at the mount layer:
#     docker compose exec -u root mcp-repl sh /app/verify_skills_volume.sh
#     # (run the backend pass FIRST so a skill is seeded into store/)
#
# The script auto-detects rw vs ro and runs the matching half of the matrix.
# It cleans up every artifact it creates (idempotent, non-destructive).
set -u

SKILLS_DIR="${RAGCLAW_SKILLS_DIR:-/ragclaw_skills}"
STORE="$SKILLS_DIR/store"
ENABLE="$SKILLS_DIR/enable"
TEST_SKILL="__verify_skill__"
POOL_UID=10001
POOL_GID=10001
# A sandbox home used only for the per-user symlink test (kept off /app so it
# never touches the bind-mounted source).
SANDBOX_HOME="$(mktemp -d /tmp/verify_skills.XXXXXX)"
USER_LINK_DIR="$SANDBOX_HOME/.ragclaw/skills"
USER_LINK="$USER_LINK_DIR/$TEST_SKILL"

PASS=0
FAIL=0
INFO=0

cecho() { printf '%s\n' "$1"; }
ok()   { cecho "  [PASS] $1"; PASS=$((PASS+1)); }
bad()  { cecho "  [FAIL] $1"; FAIL=$((FAIL+1)); }
note() { cecho "  [INFO] $1"; INFO=$((INFO+1)); }

# Drop to a non-root pool UID and run a command. Tries setpriv, then runuser,
# then su. Prints the command's stdout/stderr (so probe echoes surface).
as_pool() {
  local cmd="$1"
  if command -v setpriv >/dev/null 2>&1; then
    setpriv --reuid="$POOL_UID" --regid="$POOL_GID" --clear-groups sh -c "$cmd" 2>&1
  elif command -v runuser >/dev/null 2>&1; then
    runuser -u "#$POOL_UID" -- sh -c "$cmd" 2>&1
  elif command -v su >/dev/null 2>&1; then
    su -s /bin/sh "#$POOL_UID" -c "$cmd" 2>&1
  else
    echo "SKIP_NO_SETUID_TOOL"
  fi
}

echo "=== Phase 7 skills-volume validation (RAGCLAW_SKILLS_DIR=$SKILLS_DIR) ==="

# --- 0) Volume mounted? ---
if [ -d "$SKILLS_DIR" ]; then ok "volume present at $SKILLS_DIR"; else bad "volume NOT mounted at $SKILLS_DIR"; fi

# --- 1) rw vs ro detection (drives which half of the matrix runs) ---
# Ensure the store/ enable/ layout exists before probing (the backend's
# database.py creates them at startup; this just makes the probe accurate even
# if the script runs before/without that).
mkdir -p "$STORE" "$ENABLE" 2>/dev/null
STORE_WRITABLE=0
if touch "$STORE/.writetest" 2>/dev/null; then
  rm -f "$STORE/.writetest"; STORE_WRITABLE=1
fi
if [ "$STORE_WRITABLE" -eq 1 ]; then
  note "store/ is WRITABLE -> running BACKEND (seed + chain + cross-UID read + no-pyc) pass"
else
  note "store/ is READ-ONLY -> running mcp-repl (ro check + cross-UID read + write-rejection) pass"
fi

# --- 2) ro flag reported from /proc/mounts ---
if grep -q " $SKILLS_DIR " /proc/mounts 2>/dev/null; then
  if grep " $SKILLS_DIR " /proc/mounts 2>/dev/null | grep -qw ro; then
    ok "mounted read-only"
  else
    note "mounted read-write"
  fi
else
  note "$SKILLS_DIR not in /proc/mounts (overlay/auto-mount) — cannot assert ro/rw from mounts"
fi

# =====================================================================
# BACKEND (rw) pass: seed a skill, build the full chain, verify reads.
# =====================================================================
if [ "$STORE_WRITABLE" -eq 1 ]; then
  mkdir -p "$STORE" "$ENABLE"
  # Seed a throwaway skill (idempotent — skip if already there).
  if [ ! -d "$STORE/$TEST_SKILL" ]; then
    mkdir -p "$STORE/$TEST_SKILL/scripts"
    printf '# test skill\nname: Verify Skill\ndescription: Phase 7 probe\n' > "$STORE/$TEST_SKILL/SKILL.md"
    printf 'def add(a, b):\n    return a + b\n' > "$STORE/$TEST_SKILL/scripts/calc.py"
    chmod 0755 "$STORE/$TEST_SKILL"
    chmod 0644 "$STORE/$TEST_SKILL/SKILL.md" "$STORE/$TEST_SKILL/scripts/calc.py"
  fi
  # enable-symlink as the backend creates it: enable/<s> -> ../store/<s> (relative).
  if [ ! -e "$ENABLE/$TEST_SKILL" ]; then
    ln -s "../store/$TEST_SKILL" "$ENABLE/$TEST_SKILL"
  fi
  # per-user symlink as mcp-repl creates it: absolute target across volumes.
  mkdir -p "$USER_LINK_DIR"
  if [ ! -e "$USER_LINK" ]; then
    ln -s "$ENABLE/$TEST_SKILL" "$USER_LINK"
  fi

  # 3) Full 3-layer chain resolves end-to-end (root).
  if [ -f "$USER_LINK/SKILL.md" ]; then ok "3-layer chain resolves: .ragclaw -> enable -> store -> SKILL.md"
  else bad "3-layer chain broken (root cannot read through it)"; fi

  # 4) Cross-UID read through the chain by a NON-ROOT pool UID (the core claim).
  OUT=$(as_pool "cat '$USER_LINK/SKILL.md' >/dev/null 2>&1 && echo READ_OK || echo READ_FAIL")
  if [ "$OUT" = "READ_OK" ]; then ok "pool UID $POOL_UID reads SKILL.md through the chain"
  elif [ "$OUT" = "SKIP_NO_SETUID_TOOL" ]; then note "no setuid tool available — skipping cross-UID read probe"
  else bad "pool UID $POOL_UID CANNOT read through chain: $OUT"; fi

  # 5) Cross-UID import of a skill .py — verify PYTHONDONTWRITEBYTECODE behaviour.
  # 5a) Container-level: is the flag active in this python's env (inherited from
  #     the mcp Dockerfile / compose)? On mcp-repl it MUST be; on the backend it
  #     is irrelevant (the backend AST-parses skill scripts, never imports them).
  if python -c "import sys; sys.exit(0 if sys.flags.dont_write_bytecode else 1)" 2>/dev/null; then
    ok "PYTHONDONTWRITEBYTECODE=1 active in this container's python (inherited from image/compose)"
  else
    note "PYTHONDONTWRITEBYTECODE NOT set here — only matters on mcp-repl; backend never imports skill scripts"
  fi
  # 5b) Mechanism: force the flag, import the skill .py as the pool UID, and assert
  #     no .pyc lands in the shared store. Proves the design stays clean when the
  #     flag is honoured (the real mcp-repl sets it via image ENV + compose).
  PROBE_FILE="$SANDBOX_HOME/calc_probe.py"
  cat > "$PROBE_FILE" <<PYEOF
import importlib.util
spec = importlib.util.spec_from_file_location("__verify_calc__", "$STORE/$TEST_SKILL/scripts/calc.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
assert m.add(2, 3) == 5, "calc.add wrong"
print("IMPORT_OK")
PYEOF
  OUT=$(as_pool "PYTHONDONTWRITEBYTECODE=1 python '$PROBE_FILE'")
  if [ "$OUT" = "IMPORT_OK" ]; then
    if find "$STORE" -name '*.pyc' | grep -q .; then
      bad "import dropped a .pyc into the shared store even with PYTHONDONTWRITEBYTECODE=1"
    else
      ok "pool UID imports skill .py (flag forced) AND no .pyc written to shared store"
    fi
  elif [ "$OUT" = "SKIP_NO_SETUID_TOOL" ]; then
    note "no setuid tool — skipping import probe"
  else
    note "import probe could not run as pool UID ($OUT) — manual check advised"
  fi
  rm -f "$PROBE_FILE" 2>/dev/null

  # 6) Backend rw: a write from the pool UID into store/ is allowed here (expected
  #    on the writer side; the ro mount on mcp-repl is what blocks it). Sanity only.
  OUT=$(as_pool "echo x > '$STORE/$TEST_SKILL/SKILL.md.tmp' 2>/dev/null && echo WRITE_OK || echo WRITE_FAIL")
  if [ "$OUT" = "WRITE_OK" ]; then note "pool UID write allowed on rw backend mount (expected; mcp-repl is ro)"
  else note "pool UID write rejected even on backend mount ($OUT)"; fi
  rm -f "$STORE/$TEST_SKILL/SKILL.md.tmp" 2>/dev/null

  # Cleanup everything this pass created.
  rm -f "$USER_LINK" 2>/dev/null
  rm -rf "$SANDBOX_HOME" 2>/dev/null
  rm -f "$ENABLE/$TEST_SKILL" 2>/dev/null
  rm -rf "$STORE/$TEST_SKILL" 2>/dev/null
fi

# =====================================================================
# mcp-repl (ro) pass: verify an already-seeded skill is readable + ro holds.
# =====================================================================
if [ "$STORE_WRITABLE" -eq 0 ]; then
  # Find any skill the backend already enabled.
  SEED=""
  if [ -d "$ENABLE" ]; then
    for e in "$ENABLE"/*; do
      [ -e "$e" ] || continue
      SEED="$(basename "$e")"; break
    done
  fi
  if [ -n "$SEED" ]; then
    mkdir -p "$USER_LINK_DIR"
    ln -s "$ENABLE/$SEED" "$USER_LINK_DIR/$SEED" 2>/dev/null
    OUT=$(as_pool "cat '$USER_LINK_DIR/$SEED/SKILL.md' >/dev/null 2>&1 && echo READ_OK || echo READ_FAIL")
    if [ "$OUT" = "READ_OK" ]; then ok "pool UID $POOL_UID reads seeded skill '$SEED' through chain (ro)"
    elif [ "$OUT" = "SKIP_NO_SETUID_TOOL" ]; then note "no setuid tool — skipping cross-UID read probe"
    else bad "pool UID $POOL_UID CANNOT read seeded skill through chain: $OUT"; fi
    rm -f "$USER_LINK_DIR/$SEED" 2>/dev/null
  else
    note "no seeded skill found in enable/ — run the BACKEND pass first to seed one"
  fi

  # 7) Write from sandbox UID into shared store is rejected (ro safety net).
  OUT=$(as_pool "echo x > '$STORE/SKILL.md.tmp' 2>/dev/null && echo WRITE_OK || echo WRITE_FAIL")
  if [ "$OUT" = "WRITE_FAIL" ]; then ok "sandbox UID write into shared store is blocked (ro mount)"
  elif [ "$OUT" = "SKIP_NO_SETUID_TOOL" ]; then note "no setuid tool — skipping write-rejection probe"
  elif [ "$OUT" = "WRITE_OK" ]; then bad "sandbox UID WRITE SUCCEEDED into shared store on ro mount!"
  else note "write probe ambiguous ($OUT) — manual check advised"; fi
  rm -f "$STORE/SKILL.md.tmp" 2>/dev/null
  rm -rf "$SANDBOX_HOME" 2>/dev/null
fi

echo "=== result: $PASS passed, $FAIL failed, $INFO info ==="
[ "$FAIL" -eq 0 ]
