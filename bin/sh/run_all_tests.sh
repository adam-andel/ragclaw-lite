#!/usr/bin/env bash
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
# RAGClaw Backend Full Test Suite (container mode) — macOS / Linux
# Usage: bash bin/sh/run_all_tests.sh
#
# Runs the pytest suite inside the '<project>ragclaw-lite' Docker container. Local Python
# execution is not supported — this project must run in container mode.
# The ragclaw container is started on demand (compose up -d ragclaw) and the
# tests are executed via `compose exec`.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# Tests always run against the BASE (prod) config: the baked image plus the
# deps we install inside it. The dev overlay (bind-mount + uvicorn --reload)
# is irrelevant for `compose exec` and, if applied, would restart a live dev
# session. Force prod mode regardless of RAGCLAW_DEV / --dev.
RAGCLAW_DEV=0
COMPOSE_FILE="$ROOT/docker-compose.yml"

source "$SCRIPT_DIR/lib/common.sh"

# Leave the ragclaw container running after tests when set to 1.
RAGCLAW_KEEP_CONTAINER="${RAGCLAW_KEEP_CONTAINER:-0}"

if ! test_docker; then
  c_red "ERROR: Docker is not installed or not running. Container mode only."
  exit 1
fi

# Ensure the ragclaw container is up (it is the test execution environment).
c_yellow "Ensuring $(proj_name)ragclaw-lite container is running..."
compose up -d ragclaw || { c_red "ERROR: failed to start $(proj_name)ragclaw-lite container"; exit 1; }

# Install test deps inside the container (idempotent, non-root image).
c_yellow "[1/9] Installing dev dependencies in container (pytest, pytest-asyncio, pytest-html, httpx)..."
compose exec -T ragclaw bash -c "pip install --break-system-packages -q pytest pytest-asyncio pytest-html httpx"
[ $? -ne 0 ] && c_red "WARNING: pip install in container returned exit code $?"

passed=0; failed=0; errors=0
declare -a failure_log

c_cyan "=========================================="
c_cyan "  RAGClaw Backend Full Test Suite (container)"
c_cyan "=========================================="

# ---- Helper: run a pytest batch (inside container) and tally ----
run_batch() {
  local label="$1"; shift
  local files="$*"
  c_yellow "[$label] Running..."
  local output
  output="$(compose exec -T ragclaw bash -c "cd /app/backend && PYTHONPATH=/app/backend pytest $files -v --tb=short" 2>&1)"
  echo "$output"

  # Tally
  local m
  m="$(echo "$output" | grep -oE '[0-9]+ passed'   | grep -oE '[0-9]+')"; [ -n "$m" ] && passed=$((passed + m))
  m="$(echo "$output" | grep -oE '[0-9]+ failed'   | grep -oE '[0-9]+')"; [ -n "$m" ] && failed=$((failed + m))
  m="$(echo "$output" | grep -oE '[0-9]+ error'    | grep -oE '[0-9]+')"; [ -n "$m" ] && errors=$((errors + m))

  # Capture failure/xfail lines and final summary for end-of-run report.
  local issues final_line
  issues="$(echo "$output" | grep -E '(FAILED|XFAIL|XPASS|ERRORS)' | grep -vE '=.*=')"
  final_line="$(echo "$output" | grep -E '^=+.*(passed|failed|xfailed|error|warnings).*=+')"
  if [ -n "$issues" ] || [ -n "$final_line" ]; then
    failure_log+=("")
    failure_log+=("--- [$label] ---")
    [ -n "$issues" ]     && while IFS= read -r l; do failure_log+=("$l"); done <<< "$issues"
    [ -n "$final_line" ] && failure_log+=("$final_line")
  fi
}

# ---- Batch 1: Unit (no BGE) ----
run_batch "2/9 unit (parser/chunker/bm25/cache)" tests/unit/test_parser.py tests/unit/test_chunker.py tests/unit/test_bm25.py tests/unit/test_cache.py
# ---- Batch 2: API auth / kb / users (no BGE) ----
run_batch "3/9 api (auth/kb/users)" tests/api/test_auth.py tests/api/test_kb.py tests/api/test_users.py
# ---- Batch 3: API retrieval (needs BGE) ----
run_batch "4/9 api (retrieval)" tests/api/test_retrieval.py
# ---- Batch 4: API documents (needs BGE) ----
run_batch "5/9 api (documents)" tests/api/test_documents.py
# ---- Batch 5: API chat (needs BGE) ----
run_batch "6/9 api (chat)" tests/api/test_chat.py
# ---- Batch 6: Security auth / rbac / conversation (no BGE) ----
run_batch "7/9 security (auth/rbac/conv)" tests/security/test_auth.py tests/security/test_rbac.py tests/security/test_conversation_isolation.py
# ---- Batch 7: Security injection (needs BGE) ----
run_batch "8/9 security (injection)" tests/security/test_injection.py
# ---- Batch 8: vector_store + integration (needs BGE) ----
run_batch "9/9 unit + integration (vector_store/pipeline)" tests/unit/test_vector_store.py tests/integration/test_upload_pipeline.py

# ---- Summary ----
echo
c_cyan "=========================================="
c_cyan "  SUMMARY"
c_cyan "  Passed:  $passed"
[ "$failed" -gt 0 ] && c_red   "  Failed:  $failed"
[ "$errors" -gt 0 ] && c_red   "  Errors:  $errors"
echo -e "  Total:   $((passed + failed + errors))"
c_cyan "=========================================="

# ---- Failure / XFAIL detail log ----
if [ ${#failure_log[@]} -gt 0 ]; then
  echo
  c_magenta "=========================================="
  c_magenta "  FAILURE / XFAIL DETAILS"
  c_magenta "=========================================="
  local i
  for i in "${failure_log[@]}"; do
    echo "$i"
  done
  c_magenta "=========================================="
fi

# ---- Cleanup: stop the ragclaw container we started on demand ----
if [ "$RAGCLAW_KEEP_CONTAINER" = "1" ]; then
  c_yellow "Leaving $(proj_name)ragclaw-lite running (RAGCLAW_KEEP_CONTAINER=1)"
else
  c_yellow "Stopping $(proj_name)ragclaw-lite container used for tests..."
  compose stop ragclaw >/dev/null 2>&1 && c_green "  $(proj_name)ragclaw-lite stopped"
fi
