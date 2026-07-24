#!/usr/bin/env bash
# RAGClaw All-in-one Control Script (macOS / Linux)
# Usage: bash bin/sh/start.sh [start|stop|reload|status]
#
# Container mode only: the backend runs containerized and serves the frontend
# from the container — no local Vite / local Python is used. This project must
# run in container mode.
#
# This script drives `docker compose` directly for the base stack
# (ragclaw / mcp-repl / ragclaw-egress). It does NOT delegate to backend.sh or
# mcp_repl.sh — those remain available for per-service control.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
CONTAINER_PORT=8000   # backend container port; host port = RAGCLAW_PORT / .env / random

source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/mirror.sh"

# Open a URL in the default browser: xdg-open (Linux) preferred, else open (macOS).
open_browser() {
  local url="$1"
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$url" >/dev/null 2>&1 &
  elif command -v open >/dev/null 2>&1; then
    open "$url" >/dev/null 2>&1 &
  fi
}

# Dev-mode toggle: optional leading --dev / --prod / --watch flags (env RAGCLAW_DEV default).
RAGCLAW_DEV="${RAGCLAW_DEV:-0}"
WATCH=0
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --dev)   RAGCLAW_DEV=1 ;;
    --prod)  RAGCLAW_DEV=0 ;;
    --watch) WATCH=1 ;;
    *) c_yellow "Unknown flag: $1" ;;
  esac
  shift
done
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$RAGCLAW_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi
# --dev implies the mcp-repl hot-reload watcher (Route B, watch_mcp.sh).
if is_dev_mode; then
  WATCH=1
fi

# start builds the whole stack; ragclaw is multi-stage (node + python), the other
# services are python-only, so the union is python:3.12-slim + node:22-alpine.
# In dev mode, frontend-dev also pulls library/node:22-bookworm-slim.
REQUIRED_IMAGES=("library/python:3.12-slim" "library/node:22-alpine")
if is_dev_mode; then
  REQUIRED_IMAGES+=("library/node:22-bookworm-slim")
fi

# ---- mcp-repl hot-reload watcher (Route B) ----
# dev mode implies this is enabled; it is launched as a detached background
# process and tracked by a PID file so `stop` can clean it up together with
# the stack. It only ever does `compose restart mcp-repl` — no image rebuild,
# no effect on production.
RUN_DIR="$ROOT/.run"
WATCH_PID="$RUN_DIR/watch_mcp.pid"
WATCH_LOG="$RUN_DIR/watch_mcp.log"

start_watcher() {
  mkdir -p "$RUN_DIR"
  if [ -f "$WATCH_PID" ] && kill -0 "$(cat "$WATCH_PID")" 2>/dev/null; then
    c_yellow "  mcp-repl watcher already running (pid $(cat "$WATCH_PID")) — skipping."
    return 0
  fi
  if ! command -v inotifywait >/dev/null 2>&1 && ! command -v fswatch >/dev/null 2>&1; then
    c_yellow "  WARNING: neither 'inotifywait' (inotify-tools) nor 'fswatch' found on host —"
    c_yellow "           skipping mcp-repl hot-reload watcher."
    c_yellow "           Linux:  sudo apt-get install -y inotify-tools"
    c_yellow "           macOS:  brew install fswatch"
    return 0
  fi
  local devflag=""
  is_dev_mode && devflag="--dev"
  c_cyan "=== Starting mcp-repl hot-reload watcher ($(mode_label) mode) ==="
  nohup bash "$SCRIPT_DIR/watch_mcp.sh" $devflag > "$WATCH_LOG" 2>&1 &
  disown
  echo $! > "$WATCH_PID"
  c_dim "  watcher pid: $(cat "$WATCH_PID")   log: $WATCH_LOG"
  c_dim "  stopped together with the stack via:  bash bin/sh/start.sh stop"
}

stop_watcher() {
  if [ -f "$WATCH_PID" ]; then
    local pid="$(cat "$WATCH_PID")"
    if kill -0 "$pid" 2>/dev/null; then
      c_dim "  stopping mcp-repl watcher (pid $pid)"
      kill "$pid" 2>/dev/null
    fi
    rm -f "$WATCH_PID"
  fi
}

# ---- helpers ----
build_stack() {  # $1 = mirror
  c_cyan "=== Building stack (registry: $1, mode: $(mode_label)) ==="
  # All services consume REGISTRY (base-image mirror). frontend/Dockerfile.dev
  # now declares ARG REGISTRY too, so it gets the same mirror as everything else.
  compose build --build-arg REGISTRY="$1" ragclaw mcp-repl ragclaw-egress || return 1
  if is_dev_mode; then
    c_cyan "=== Building frontend-dev (Vite HMR) ==="
    compose build --build-arg REGISTRY="$1" frontend-dev || return 1
  fi
}

  up_stack() {  # $1 = "force" to --force-recreate
  local recreate=""
  [ "${1:-}" = "force" ] && recreate="--force-recreate"
  repair_egress_network
  if ! compose up -d $recreate; then
    c_yellow "  First attempt failed; releasing ragclaw_ragclaw-internal network lease and retrying..."
    repair_egress_network force
    compose up -d
  fi
}

# Actual host port docker published for the ragclaw container. Works for all three
# cases uniformly: inline RAGCLAW_PORT, .env RAGCLAW_PORT, or a random ephemeral port.
# Requires the container to be up (call after up_stack).
ragclaw_published_port() {
  local p
  p="$(compose port ragclaw "$CONTAINER_PORT" 2>/dev/null | sed -E 's#.*:##')"
  echo "${p:-$CONTAINER_PORT}"
}

# Actual host port docker published for the Vite HMR frontend (frontend-dev).
# When RAGCLAW_FRONTEND_PORT is set the port is pinned; when unset docker assigns
# a RANDOM free host port, so we MUST ask docker rather than assume 5173. Only
# meaningful in --dev mode (frontend-dev lives in the dev overlay).
frontend_published_port() {
  local p
  p="$(compose port frontend-dev 5173 2>/dev/null | sed -E 's#.*:##')"
  echo "${p:-5173}"
}

# ---- actions ----
case "${1:-start}" in
  start)
    assert_docker
    if [ ! -f "$COMPOSE_FILE" ]; then
      c_red "ERROR: docker-compose.yml not found at $COMPOSE_FILE"
      exit 1
    fi
    mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    build_stack "$mirror" || exit 1
    echo
    c_cyan "=== Starting stack ==="
    up_stack || exit 1
    [ "$WATCH" = "1" ] && start_watcher
    PORT="$(ragclaw_published_port)"
    wait_for_backend
    echo
    c_green "=== All services started (Docker mode) ==="
    c_dim "  App:     http://localhost:$PORT"
    c_dim "  Swagger: http://127.0.0.1:$PORT/docs"
    c_dim "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)"
    if is_dev_mode; then
      c_dim "  Frontend (HMR): http://localhost:$(frontend_published_port)  (Vite dev server)"
    fi
    open_url="http://localhost:$PORT"
    is_dev_mode && open_url="http://localhost:$(frontend_published_port)"
    sleep 1
    open_browser "$open_url"
    ;;

  reload)
    assert_docker
    if [ ! -f "$COMPOSE_FILE" ]; then
      c_red "ERROR: docker-compose.yml not found at $COMPOSE_FILE"
      exit 1
    fi
    mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    build_stack "$mirror" || exit 1
    echo
    c_cyan "=== Recreating stack ==="
    up_stack force || exit 1
    [ "$WATCH" = "1" ] && start_watcher
    PORT="$(ragclaw_published_port)"
    wait_for_backend
    echo
    c_green "=== Reload complete (Docker mode) ==="
    c_dim "  App: http://localhost:$PORT"
    open_url="http://localhost:$PORT"
    is_dev_mode && open_url="http://localhost:$(frontend_published_port)"
    sleep 1
    open_browser "$open_url"
    ;;

  stop)
    stop_watcher
    c_cyan "=== Stopping all services ==="
    if compose stop; then
      c_green "All services stopped (Docker)"
    fi
    ;;

  status)
    c_cyan "=== RAGClaw Service Status ==="
    c_cyan "  Mode: Docker container"
    compose ps
    if [ -n "$(compose ps -q ragclaw 2>/dev/null)" ]; then
      PORT="$(ragclaw_published_port)"
      c_dim "  App URL: http://localhost:$PORT  (RAGCLAW_PORT: ${RAGCLAW_PORT:-<random>})"
    fi
    ;;

  *)
    c_yellow "Usage: bash bin/sh/start.sh [--dev|--prod] [start|stop|reload|status]"
    c_yellow "  --dev  implies the mcp-repl hot-reload watcher (no separate terminal needed)"
    ;;
esac
