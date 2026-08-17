#!/usr/bin/env bash
# init.sh — optional per-skill initialization hook (ragclaw adapter).
#
# The backend executes this script ONCE whenever the skill is enabled
# (enable_skill_fs) or re-uploaded / replaced (replace_skill_folder). It is the
# unified trigger point for any per-skill setup work, so ALL adaptations to the
# third-party anysearch skill live here. The third-party skill source itself
# (SKILL.md, scripts/, ...) is NEVER edited by this script or by the backend —
# only this adapter and the ragclaw-owned files under .ragclaw/ are touched.
#
# Contract:
#   - Runs with cwd = the skill folder (SKILL_DIR derived from BASH_SOURCE).
#   - Must be idempotent; failures are logged and swallowed by the backend.
#   - The backend re-applies world-readable chmod afterwards so the
#     (different-UID) REPL sandbox can read any artifacts this script writes.

set -euo pipefail

# This script lives in <skill>/.ragclaw/, so SKILL_DIR must step up one level to
# reach the package root. Ragclaw-owned artifacts (shim.py / adapter.json / the
# committed SKILL.ragclaw.md) live under $SKILL_DIR/.ragclaw/, never in the
# native tree.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$SKILL_DIR/runtime.conf.example"
OUT="$SKILL_DIR/runtime.conf"
folder="$(basename "$SKILL_DIR")"

# ---------------------------------------------------------------------------
# 0) Detect the native CLI and compute the resolved command.
#
# The <skill_dir> / <detected_command> placeholders are resolved to the
# PER-USER, permission-correct sandbox path via $REPL_SKILLS_DIR — NOT the
# backend's read-only store volume (/ragclaw_skills/store). runtime.conf is read
# by the LLM inside the REPL subprocess (run_shell / run_python), where
# REPL_SKILLS_DIR is injected per-envelope-uid by _inject_skills_dir(); the
# store volume is mounted read-only in the REPL and must NOT be the execution
# path. Keep $REPL_SKILLS_DIR literal here so it expands at run_shell time.
# ---------------------------------------------------------------------------

# Print the CLI script name to use, preferring <folder>_cli.<ext> then
# *_cli.<ext> then the first matching file. Echoes empty string when none found.
detect_cli() {
    local ext="$1"
    if [ -f "$SKILL_DIR/scripts/${folder}_cli.$ext" ]; then
        echo "${folder}_cli.$ext"; return
    fi
    for f in "$SKILL_DIR"/scripts/*_cli."$ext"; do
        [ -e "$f" ] || continue
        echo "$(basename "$f")"; return
    done
    for f in "$SKILL_DIR"/scripts/*."$ext"; do
        [ -e "$f" ] || continue
        echo "$(basename "$f")"; return
    done
    echo ""
}

RUNTIME=""
VANILLA_CMD=""

py_name="$(detect_cli py)"
if [ -n "$py_name" ]; then
    RUNTIME="python"
    VANILLA_CMD="python3 \$REPL_SKILLS_DIR/$folder/scripts/$py_name"
fi

if [ -z "$RUNTIME" ]; then
    js_name="$(detect_cli js)"
    if [ -n "$js_name" ]; then
        RUNTIME="node"
        VANILLA_CMD="node \$REPL_SKILLS_DIR/$folder/scripts/$js_name"
    fi
fi

if [ -z "$RUNTIME" ]; then
    sh_name="$(detect_cli sh)"
    if [ -n "$sh_name" ]; then
        RUNTIME="bash"
        VANILLA_CMD="bash \$REPL_SKILLS_DIR/$folder/scripts/$sh_name"
    fi
fi

# Resolved command: ALWAYS route through the secret-zero shim when the ragclaw
# adapter files exist, regardless of whether a KEY is configured. The shim keeps
# the KEY out of the sandbox (it lives only in the egress injection proxy) and
# forwards anonymously when no KEY is set, so there is no KEY-dependent branch.
# If the adapter files are missing the skill is un-adapted, so we fall back to the
# native CLI directly (vanilla).
RESOLVED_CMD="$VANILLA_CMD"
if [ -f "$SKILL_DIR/.ragclaw/shim.py" ] && [ -f "$SKILL_DIR/.ragclaw/adapter.json" ]; then
    RESOLVED_CMD="python3 \$REPL_SKILLS_DIR/$folder/.ragclaw/shim.py"
fi

# ---------------------------------------------------------------------------
# 1) Materialise runtime.conf from runtime.conf.example (if present).
# ---------------------------------------------------------------------------
if [ ! -f "$EXAMPLE" ]; then
    echo "[.ragclaw/init] no runtime.conf.example; skipping runtime.conf"
elif [ -z "$RUNTIME" ]; then
    echo "[.ragclaw/init] no CLI script found under scripts/; skipping runtime.conf"
else
    sed -e "s|<detected_runtime>|$RUNTIME|g" \
        -e "s|<detected_command>|$RESOLVED_CMD|g" \
        "$EXAMPLE" > "$OUT"
    chmod 644 "$OUT"
    echo "[.ragclaw/init] wrote $OUT (Runtime=$RUNTIME, Command=$RESOLVED_CMD)"
fi

# ---------------------------------------------------------------------------
# 2) The ragclaw-owned adapter doc (.ragclaw/SKILL.ragclaw.md) is now committed
#    statically in this directory (not generated at runtime): under always-shim
#    the resolved command is a fixed value, so there is nothing to compute. The
#    loader injects it as an EXTRA context block and NEVER touches the
#    third-party SKILL.md. Keep it lean — see the committed file for content.
# ---------------------------------------------------------------------------

echo "[.ragclaw/init] done"
