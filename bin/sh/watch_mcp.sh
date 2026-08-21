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
# =====================================================================
# RAGClaw — dev-only hot-reload for the REPL MCP server (Route B).
#
# Watches the HOST copy of ./mcp (the bind-mount source for the mcp-repl
# container) and runs `compose restart mcp-repl` whenever a server source file
# changes — no image rebuild, no watchdog in the image, and ZERO effect on
# production (prod never loads docker-compose.dev.yml, and this script never
# touches the image / requirements.txt).
#
# On Linux the watch uses inotify-tools (`inotifywait`); on macOS it uses
# `fswatch` (install via `brew install fswatch`). The script auto-detects
# whichever is available.
#
# Usage:
#   bash bin/sh/watch_mcp.sh [--dev|--prod]
#
#   --dev   Also load docker-compose.dev.yml so the restart targets the
#           bind-mounted (dev) mcp-repl. This is the intended mode here.
#   --prod  Use base docker-compose.yml only (not usually what you want for a
#           watched dev loop, but harmless if you run mcp-repl from prod base).
#
# Host dependencies:
#   Linux:  sudo apt-get install -y inotify-tools
#   macOS:  brew install fswatch
#
# Run from the project root on a native filesystem. On Windows, stay on the
# WSL2 ext4 mount (//wsl$/... will NOT receive inotify events over 9P).
# =====================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
WATCH_DIR="$ROOT/mcp"

source "$SCRIPT_DIR/lib/common.sh"

# ---- Dev/prod toggle (mirrors start.sh / mcp_repl.sh) ----
RAGCLAW_DEV="${RAGCLAW_DEV:-0}"
case "${1:-}" in
  --dev)  RAGCLAW_DEV=1; shift ;;
  --prod) RAGCLAW_DEV=0; shift ;;
esac
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$RAGCLAW_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi

# Minimum quiet gap (seconds) after a restart before the next one is allowed.
# Coalesces a burst of saves into a single restart.
RESTART_INTERVAL=2

SVC=mcp-repl

# ---- pre-flight ----
if command -v inotifywait >/dev/null 2>&1; then
  WATCHER=inotifywait
elif command -v fswatch >/dev/null 2>&1; then
  WATCHER=fswatch
else
  c_red "ERROR: neither 'inotifywait' (inotify-tools) nor 'fswatch' found on the host."
  c_yellow "       Linux:  sudo apt-get install -y inotify-tools"
  c_yellow "       macOS:  brew install fswatch"
  exit 1
fi
if [ ! -d "$WATCH_DIR" ]; then
  c_red "ERROR: watch directory not found: $WATCH_DIR"
  exit 1
fi
assert_docker

# Fail loudly (not silent-loop) if the target container isn't up.
if [ -z "$(compose ps -q "$SVC" 2>/dev/null)" ]; then
  c_yellow "WARNING: $SVC is not running. The watcher will still watch, but"
  c_yellow "         restarts will no-op until you start it, e.g.:"
  c_yellow "           bash bin/sh/start.sh --dev start"
fi

# ---- FIFO bridge to the watcher (so we control its PID for cleanup) ----
FIFO="$(mktemp -u)"
mkfifo "$FIFO"

cleanup() {
  c_dim ""
  c_cyan "Stopping mcp-repl watcher."
  [ -n "${WATCHER_PID:-}" ] && kill "$WATCHER_PID" >/dev/null 2>&1
  exec 3<&- 2>/dev/null
  rm -f "$FIFO"
  exit 0
}
trap cleanup INT TERM

echo
c_cyan "=== mcp-repl hot-reload watcher ($(mode_label) mode) ==="
c_dim "  Watching : $WATCH_DIR"
c_dim "  On change: compose restart $SVC"
c_dim "  Ctrl-C to stop."
echo

# Exclude the workspace overlay dir (named volume mounts on top of
# /app/workspace; the host lower dir must not trigger restarts) and Python
# bytecode caches. We watch recursively so new source files are picked up too,
# but the exclude keeps volume/user artifacts out.
if [ "$WATCHER" = "inotifywait" ]; then
  inotifywait -m -r \
    -e close_write -e moved_to -e create \
    --exclude '(/workspace/|__pycache__/|\.pyc$|requirements\.lock$|requirements\.txt$)' \
    --format '%w%f' \
    "$WATCH_DIR" > "$FIFO" &
  WATCHER_PID=$!
else
  # fswatch (macOS / BSD). -E = extended regex; limit to create/update/rename
  # events so saves coalesce into a single restart like inotifywait does.
  fswatch -r -E \
    -e '/workspace/' -e '__pycache__' -e '\.pyc$' \
    -e 'requirements\.lock$' -e 'requirements\.txt$' \
    --event Created --event Updated --event Renamed \
    "$WATCH_DIR" > "$FIFO" &
  WATCHER_PID=$!
fi

exec 3<"$FIFO"

# Drain any events buffered during a restart so a burst = one restart.
drain() {
  while read -r -t 0 -u 3 _; do :; done
}

while read -r -u 3 file; do
  c_cyan "▸ changed: ${file#"$WATCH_DIR"/} — restarting $SVC"
  if compose restart "$SVC"; then
    c_green "  restarted."
  else
    c_yellow "  restart failed (is $SVC up? check: docker logs $(proj_name)ragclaw-mcp-repl)"
  fi
  drain
  [ "$RESTART_INTERVAL" -gt 0 ] && sleep "$RESTART_INTERVAL"
  drain
done

cleanup
