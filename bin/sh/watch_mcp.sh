#!/usr/bin/env bash
# =====================================================================
# RAGClaw — dev-only hot-reload for the REPL MCP server (Route B).
#
# Watches the HOST copy of ./mcp (the bind-mount source for the mcp-repl
# container) with inotifywait and runs `compose restart mcp-repl` whenever a
# server source file changes — no image rebuild, no watchdog in the image, and
# ZERO effect on production (prod never loads docker-compose.dev.yml, and this
# script never touches the image / requirements.txt).
#
# Usage:
#   bash bin/sh/watch_mcp.sh [--dev|--prod]
#
#   --dev   Also load docker-compose.dev.yml so the restart targets the
#           bind-mounted (dev) mcp-repl. This is the intended mode here.
#   --prod  Use base docker-compose.yml only (not usually what you want for a
#           watched dev loop, but harmless if you run mcp-repl from prod base).
#
# Requires inotify-tools (`inotifywait`) on the WSL/Linux host:
#   sudo apt-get install -y inotify-tools
#
# Run from the WSL project root (ext4, native inotify). On a Windows D:\ or
# \\wsl$\ 9P path inotify will NOT receive events — stay on the ext4 mount.
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
if ! command -v inotifywait >/dev/null 2>&1; then
  c_red "ERROR: 'inotifywait' not found on the host."
  c_yellow "       Install inotify-tools:  sudo apt-get install -y inotify-tools"
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

# ---- FIFO bridge to inotifywait (so we control its PID for cleanup) ----
FIFO="$(mktemp -u)"
mkfifo "$FIFO"

cleanup() {
  c_dim ""
  c_cyan "Stopping mcp-repl watcher."
  [ -n "${INOTIFY_PID:-}" ] && kill "$INOTIFY_PID" >/dev/null 2>&1
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
inotifywait -m -r \
  -e close_write -e moved_to -e create \
  --exclude '(/workspace/|__pycache__/|\.pyc$)' \
  --format '%w%f' \
  "$WATCH_DIR" > "$FIFO" &
INOTIFY_PID=$!

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
    c_yellow "  restart failed (is $SVC up? check: docker logs $(proj_name)-mcp-repl)"
  fi
  drain
  [ "$RESTART_INTERVAL" -gt 0 ] && sleep "$RESTART_INTERVAL"
  drain
done

cleanup
