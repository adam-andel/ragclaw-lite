#!/usr/bin/env bash
# init.sh — optional per-skill initialization hook (ragclaw adapter).
#
# The backend executes this script ONCE whenever the skill is enabled
# (enable_skill_fs) or re-uploaded / replaced (replace_skill_folder). It is the
# unified trigger point for any per-skill setup work, so ALL adaptations to the
# third-party anysearch skill live here. The third-party skill source itself is
# NEVER edited by the backend or by hand — only this adapter touches it.
#
# Contract:
#   - Runs with cwd = the skill folder (SKILL_DIR derived from BASH_SOURCE).
#   - Must be idempotent; failures are logged and swallowed by the backend.
#   - The backend re-applies world-readable chmod afterwards so the
#     (different-UID) REPL sandbox can read any artifacts this script writes.

set -euo pipefail

# This script lives in <skill>/.ragclaw/, so SKILL_DIR must step up one level to
# reach the package root. Ragclaw-owned artifacts (shim.py / adapter.json for
# secret-zero) live under $SKILL_DIR/.ragclaw/, never in the native tree.
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE="$SKILL_DIR/runtime.conf.example"
OUT="$SKILL_DIR/runtime.conf"

# Secret-zero: the backend tells us whether an API KEY is configured for this
# skill via RAGCLAW_SKILL_API_KEY_STATUS (empty | set). When "set" AND the
# ragclaw shim/adapter are present, the resolved command routes through the
# injection proxy (KEY never enters the sandbox). Otherwise we fall back to the
# native CLI (vanilla) — identical to the no-KEY behaviour.
API_KEY_STATUS="${RAGCLAW_SKILL_API_KEY_STATUS:-empty}"
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

# Resolved command: route through the secret-zero shim when a KEY is configured
# and the ragclaw adapter files exist; otherwise use the native CLI (vanilla).
RESOLVED_CMD="$VANILLA_CMD"
if [ "$API_KEY_STATUS" = "set" ] \
   && [ -f "$SKILL_DIR/.ragclaw/shim.py" ] \
   && [ -f "$SKILL_DIR/.ragclaw/adapter.json" ]; then
    RESOLVED_CMD="python3 \$REPL_SKILLS_DIR/$folder/.ragclaw/shim.py"
elif [ "$API_KEY_STATUS" = "set" ]; then
    echo "[.ragclaw/init] WARN: api_key set but .ragclaw/shim.py or adapter.json missing; falling back to native CLI"
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
# 2) Idempotently append ragclaw adapter sections to SKILL.md:
#      - ## API Endpoint Contract   (direct HTTP call reference)
#      - ## Output requirements      (keep source links + date discipline)
#      - ## Resolved command         (pre-injected CLI, skips the discovery round)
#    A sentinel comment marks the block so re-runs never duplicate it. If an
#    older manual edit of these sections exists (no sentinel), it is stripped
#    first so this script becomes the single source of truth.
# ---------------------------------------------------------------------------
SKILL_MD="$SKILL_DIR/SKILL.md"
SENTINEL="ragclaw-adapter:anysearch"

if [ -f "$SKILL_MD" ]; then
    if grep -q "$SENTINEL" "$SKILL_MD"; then
        echo "[.ragclaw/init] SKILL.md adapter block already present; skipping"
    else
        # Self-heal: remove a legacy manual edit of these sections (no sentinel).
        if grep -q "^## API Endpoint Contract" "$SKILL_MD"; then
            sed -i '/^## API Endpoint Contract/,$d' "$SKILL_MD"
            echo "[.ragclaw/init] stripped legacy manual adapter sections from $SKILL_MD"
        fi
        cat >> "$SKILL_MD" <<'RAGCLAW_EOF'

<!-- ragclaw-adapter:anysearch -->
## API Endpoint Contract

The bundled CLIs are thin wrappers over a single JSON-RPC 2.0 endpoint. Use the
CLI in normal operation; this contract is the source of truth for direct HTTP
calls (e.g. debugging, or when no runtime is available).

- **Endpoint:** `POST https://api.anysearch.com/mcp`
- **Headers:**
  - `Content-Type: application/json` (required)
  - `X-Anysearch-Client: skill/3.0.1` (required; identifies the skill spec version)
  - `Authorization: Bearer <ANYSEARCH_API_KEY>` (only when a key is set; anonymous access works without it)
- **Body (JSON-RPC 2.0):**

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "search",
    "arguments": {
      "query": "latest AI news",
      "max_results": 5
    }
  }
}
```

- **Tools (`params.name`) and `arguments`:**
  - `search` — `{ query: string, max_results?: number (1-10, clamped to 10), domain?: string, sub_domain?: string, sub_domain_params?: object | string }`
  - `get_sub_domains` — `{ domain?: string, domains?: string[] }`
  - `extract` — `{ url: string }`
  - `batch_search` — `{ queries: Array<{ query: string, domain?, sub_domain?, sub_domain_params?, max_results? }> }`
- **Notes:**
  - Anonymous access is allowed (lower rate limits); no API key required.
  - `max_results` is clamped to 10 by the CLI - pass a value in 1-10.
  - For vertical-domain queries, call `get_sub_domains` first to discover the correct `sub_domain` and required `sub_domain_params`, then pass them to `search` / `batch_search`.
  - The response is a JSON-RPC envelope; read `result` for the tool output.

## Output requirements

When you present search results to the user, you MUST follow these hard rules:
1. **Keep source links as markdown.** Every cited result MUST carry a clickable markdown link in the answer body: `[source name](full URL)` — e.g. `[China News](https://www.chinanews.com.cn/xxx)`. Never reduce a source to plain text (e.g. "Source: China News") and never drop the URL. The URL must be the real, complete, reachable link from the search result, not fabricated.
2. **List each result with its own link.** When a search returns multiple items, enumerate them and give every item its own source link.
3. **Distinguish the current date from the publish date.** The system injects the **current date** into the task background (reference only). When you say "today", mean that injected current date. A search result item's own publication time (publish date) is a SEPARATE fact — label it as the item's publish date, and NEVER write a result's publish date as "today", nor write the current date as a result's publish date.

## Resolved command (ragclaw pre-injected)

Resolved command (use directly): RESOLVED_CMD_PLACEHOLDER
RAGCLAW_EOF
        # Substitute the resolved command (shim when KEY set, else native CLI)
        # into the placeholder. $RESOLVED_CMD carries a literal $REPL_SKILLS_DIR
        # that expands at REPL run_shell time.
        sed -i "s|RESOLVED_CMD_PLACEHOLDER|$RESOLVED_CMD|g" "$SKILL_MD"
        chmod 644 "$SKILL_MD"
        echo "[.ragclaw/init] appended adapter block to $SKILL_MD"
    fi
fi

echo "[.ragclaw/init] done"
