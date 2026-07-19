#!/usr/bin/env bash
# ERAG Backend Control Script (WSL / Linux)
# Usage: bash bin/sh/backend.sh [start|stop|reload|status|build|logs]
#
# Container mode only: the backend always runs as a Docker container
# (erag-lite). Local Python / uvicorn execution is not supported — this
# project must run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down erag

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

REQUIRED_IMAGES=("library/python:3.12-slim" "library/node:22-alpine")

# ---- helpers ----
test_compose_available() {
  [ -f "$COMPOSE_FILE" ] || return 1
  grep -qE '^[[:space:]]+erag:' "$COMPOSE_FILE" && \
    grep -q 'container_name:[[:space:]]*erag-lite' "$COMPOSE_FILE"
}

test_docker_backend() {
  test_docker || return 1
  [ -n "$(docker ps -q -f name=erag-lite 2>/dev/null)" ]
}

test_backend() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"
  [ "$code" = "200" ]
}

# ---- actions ----
start_docker_backend() {
  assert_docker
  c_cyan "=== ERAG Backend (Docker :$PORT) ==="
  if test_docker_backend; then
    c_yellow "Backend already running on :$PORT (Docker mode)"
    return 0
  fi
  if ! test_compose_available; then
    c_red "ERROR: docker-compose.yml missing or lacks 'erag' service"
    return 1
  fi
  local build_mirror
  build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
  if [ -z "$build_mirror" ]; then
    c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
    return 1
  fi
  c_cyan "=== Building (registry: $build_mirror) ==="
  compose build --build-arg REGISTRY="$build_mirror" erag || { c_red "ERROR: build failed"; return 1; }
  echo
  c_cyan "=== Starting container ==="
  compose up -d erag || { c_red "ERROR: docker compose up failed"; return 1; }
  if is_dev_mode; then
    c_dim "  Dev overlay: ./backend bind-mounted, uvicorn --reload active"
    c_dim "  Edit backend/*.py to hot-restart (no image rebuild needed)"
  fi
  wait_for_backend
}

stop_docker_backend() {
  c_cyan "=== Stopping backend (Docker) ==="
  if test_docker_backend; then
    compose stop erag && c_green "Backend stopped (Docker)"
  else
    c_yellow "Backend not running (Docker)"
  fi
}

show_status() {
  c_cyan "=== ERAG Backend Status ==="
  c_dim "  Port: $PORT"
  c_cyan "  Mode: Docker container (container mode only)"
  if test_docker_backend; then
    c_green "  Status: running (erag-lite)"
    local started
    started="$(docker inspect erag-lite --format '{{.State.StartedAt}}' 2>/dev/null)"
    [ -n "$started" ] && c_dim "  Since:  $started"
    return 0
  fi
  if test_docker; then
    c_yellow "  Docker: available (not running)"
  else
    c_yellow "  Docker: not installed"
  fi
  c_red "  Status: NOT running"
}

# ---- dispatch ----
ACTION="${1:-start}"
case "$ACTION" in
  start)
    start_docker_backend
    ;;
  stop)
    stop_docker_backend
    ;;
  status)
    show_status
    ;;
  build)
    assert_docker
    build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$build_mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    c_dim "Rebuilding erag image (registry: $build_mirror, --no-cache) ..."
    compose build --build-arg REGISTRY="$build_mirror" --no-cache erag
    ;;
  reload)
    assert_docker
    if ! test_compose_available; then
      c_red "ERROR: docker-compose.yml missing or lacks 'erag' service"
      exit 1
    fi
    test_docker_backend && stop_docker_backend
    build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$build_mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    c_cyan "=== Building (registry: $build_mirror) ==="
    compose build --build-arg REGISTRY="$build_mirror" erag || { c_red "ERROR: build failed"; exit 1; }
    echo
    c_cyan "=== Starting container ==="
    compose up -d erag || { c_red "ERROR: docker compose up failed"; exit 1; }
    wait_for_backend
    ;;
  logs)
    if test_docker_backend; then
      compose logs --tail=50 -f erag-lite
    else
      c_yellow "Backend not running in Docker mode"
    fi
    ;;
  *)
    c_yellow "Usage: bash bin/sh/backend.sh [--dev|--prod] [start|stop|reload|status|build|logs]"
    echo
    echo "  --dev   Use docker-compose.dev.yml overlay (bind-mount + --reload)"
    echo "  --prod  Force base docker-compose.yml (default)"
    echo
    echo "  start   Start backend (build + up, container mode)"
    echo "  stop    Stop backend"
    echo "  reload  Stop, rebuild image (uses cache), and start backend"
    echo "  status  Show running status (Docker)"
    echo "  build   Rebuild Docker image only (--no-cache)"
    echo "  logs    Tail Docker container logs"
    ;;
esac
