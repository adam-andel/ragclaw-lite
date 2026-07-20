#!/usr/bin/env bash
# RAGClaw All-in-one Control Script (WSL / Linux)
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

# Dev-mode toggle: optional leading --dev / --prod flag (env RAGCLAW_DEV default).
RAGCLAW_DEV="${RAGCLAW_DEV:-0}"
case "${1:-}" in
  --dev)  RAGCLAW_DEV=1; shift ;;
  --prod) RAGCLAW_DEV=0; shift ;;
esac
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$RAGCLAW_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi

# start builds the whole stack; ragclaw is multi-stage (node + python), the other
# services are python-only, so the union is python:3.12-slim + node:22-alpine.
# In dev mode, frontend-dev also pulls library/node:22-bookworm-slim.
REQUIRED_IMAGES=("library/python:3.12-slim" "library/node:22-alpine")
if is_dev_mode; then
  REQUIRED_IMAGES+=("library/node:22-bookworm-slim")
fi

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
    PORT="$(ragclaw_published_port)"
    wait_for_backend
    echo
    c_green "=== All services started (Docker mode) ==="
    c_dim "  App:     http://localhost:$PORT"
    c_dim "  Swagger: http://127.0.0.1:$PORT/docs"
    c_dim "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)"
    if is_dev_mode; then
      c_dim "  Frontend (HMR): http://localhost:5173  (Vite dev server)"
    fi
    open_url="http://localhost:$PORT"
    is_dev_mode && open_url="http://localhost:5173"
    sleep 1
    command -v xdg-open >/dev/null 2>&1 && xdg-open "$open_url" >/dev/null 2>&1 &
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
    PORT="$(ragclaw_published_port)"
    wait_for_backend
    echo
    c_green "=== Reload complete (Docker mode) ==="
    c_dim "  App: http://localhost:$PORT"
    open_url="http://localhost:$PORT"
    is_dev_mode && open_url="http://localhost:5173"
    sleep 1
    command -v xdg-open >/dev/null 2>&1 && xdg-open "$open_url" >/dev/null 2>&1 &
    ;;

  stop)
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
    ;;
esac
