#!/usr/bin/env bash
# ERAG All-in-one Control Script (WSL / Linux)
# Usage: bash bin/sh/start.sh [start|stop|reload|status]
#
# Container mode only: the backend runs containerized and serves the frontend
# from the container — no local Vite / local Python is used. This project must
# run in container mode.
#
# This script drives `docker compose` directly for the base stack
# (erag / mcp-repl / erag-egress). It does NOT delegate to backend.sh or
# mcp_repl.sh — those remain available for per-service control.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT=8000

source "$SCRIPT_DIR/lib/common.sh"
source "$SCRIPT_DIR/lib/mirror.sh"

# Dev-mode toggle: optional leading --dev / --prod flag (env ERAG_DEV default).
ERAG_DEV="${ERAG_DEV:-0}"
case "${1:-}" in
  --dev)  ERAG_DEV=1; shift ;;
  --prod) ERAG_DEV=0; shift ;;
esac
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$ERAG_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi

# start builds the whole stack; erag is multi-stage (node + python), the other
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
  compose build --build-arg REGISTRY="$1" erag mcp-repl erag-egress || return 1
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
    c_yellow "  First attempt failed; releasing erag-internal network lease and retrying..."
    repair_egress_network force
    compose up -d
  fi
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
    wait_for_backend
    echo
    c_green "=== All services started (Docker mode) ==="
    c_dim "  App:     http://localhost:8000"
    c_dim "  Swagger: http://127.0.0.1:8000/docs"
    c_dim "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)"
    if is_dev_mode; then
      c_dim "  Frontend (HMR): http://localhost:5173  (Vite dev server)"
    fi
    open_url="http://localhost:8000"
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
    wait_for_backend
    echo
    c_green "=== Reload complete (Docker mode) ==="
    c_dim "  App: http://localhost:8000"
    open_url="http://localhost:8000"
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
    c_cyan "=== ERAG Service Status ==="
    c_cyan "  Mode: Docker container"
    compose ps
    ;;

  *)
    c_yellow "Usage: bash bin/sh/start.sh [--dev|--prod] [start|stop|reload|status]"
    ;;
esac
