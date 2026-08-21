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
# RAGClaw Backend Control Script (macOS / Linux)
# Usage: bash bin/sh/backend.sh [start|stop|reload|status|build|logs]
#
# Container mode only: the backend always runs as a Docker container
# (<project>ragclaw-lite). Local Python / uvicorn execution is not supported — this
# project must run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down ragclaw

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT=8000

source "$SCRIPT_DIR/lib/common.sh"

# Build sources (all default to the OFFICIAL source). Pass only to use a mirror;
# NO reachability probing is done.
#   --registry <domain>  Docker base-image registry (empty -> docker.io).
#   --apt <url>          Debian apt mirror base URL (empty -> distro default).
#   --pypi <url>         PyPI index URL (empty -> official pypi.org).
REGISTRY_ARG=""
APT_MIRROR=""
PYPI_MIRROR=""

# Dev-mode toggle: optional leading --dev / --prod flag (env RAGCLAW_DEV default).
RAGCLAW_DEV="${RAGCLAW_DEV:-0}"
while [[ "${1:-}" == --* ]]; do
  case "$1" in
    --dev)      RAGCLAW_DEV=1 ;;
    --prod)     RAGCLAW_DEV=0 ;;
    --registry) REGISTRY_ARG="${2:-}"; shift ;;
    --apt)      APT_MIRROR="${2:-}";  shift ;;
    --pypi)     PYPI_MIRROR="${2:-}"; shift ;;
    *) c_yellow "Unknown flag: $1" ;;
  esac
  shift
done
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$RAGCLAW_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi

# Populate the global BUILD_ARGS array from the explicit source vars (only inject
# when set, so the common official-source path keeps all FROM/apt/pip layers cached).
build_args() {
  BUILD_ARGS=()
  [ -n "$REGISTRY_ARG" ] && BUILD_ARGS+=( --build-arg "REGISTRY=$REGISTRY_ARG" )
  [ -n "$APT_MIRROR" ]   && BUILD_ARGS+=( --build-arg "APT_MIRROR=$APT_MIRROR" )
  [ -n "$PYPI_MIRROR" ]  && BUILD_ARGS+=( --build-arg "PYPI_MIRROR=$PYPI_MIRROR" )
}

# ---- helpers ----
test_compose_available() {
  [ -f "$COMPOSE_FILE" ] || return 1
  grep -qE '^[[:space:]]+ragclaw:' "$COMPOSE_FILE" && \
    grep -qF 'container_name: ${COMPOSE_PROJECT_NAME}ragclaw-lite' "$COMPOSE_FILE"
}

test_docker_backend() {
  test_docker || return 1
  [ -n "$(docker ps -q -f "name=$(proj_name)ragclaw-lite" 2>/dev/null)" ]
}

test_backend() {
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:$PORT/api/health" 2>/dev/null)"
  [ "$code" = "200" ]
}

# ---- actions ----
start_docker_backend() {
  assert_docker
  c_cyan "=== RAGClaw Backend (Docker :$PORT) ==="
  if test_docker_backend; then
    c_yellow "Backend already running on :$PORT (Docker mode)"
    return 0
  fi
  if ! test_compose_available; then
    c_red "ERROR: docker-compose.yml missing or lacks 'ragclaw' service"
    return 1
  fi
  build_args
  c_cyan "=== Building (registry: ${REGISTRY_ARG:-<official>}, apt: ${APT_MIRROR:-<official>}, pypi: ${PYPI_MIRROR:-<official>}) ==="
  compose build --progress=plain "${BUILD_ARGS[@]}" ragclaw || { c_red "ERROR: build failed"; return 1; }
  echo
  c_cyan "=== Starting container ==="
  compose up -d ragclaw || { c_red "ERROR: docker compose up failed"; return 1; }
  if is_dev_mode; then
    c_dim "  Dev overlay: ./backend bind-mounted, uvicorn --reload active"
    c_dim "  Edit backend/*.py to hot-restart (no image rebuild needed)"
  fi
  wait_for_backend
}

stop_docker_backend() {
  c_cyan "=== Stopping backend (Docker) ==="
  if test_docker_backend; then
    compose stop ragclaw && c_green "Backend stopped (Docker)"
  else
    c_yellow "Backend not running (Docker)"
  fi
}

show_status() {
  c_cyan "=== RAGClaw Backend Status ==="
  c_dim "  Port: $PORT"
  c_cyan "  Mode: Docker container (container mode only)"
  if test_docker_backend; then
    c_green "  Status: running ($(proj_name)ragclaw-lite)"
    local started
    started="$(docker inspect "$(proj_name)ragclaw-lite" --format '{{.State.StartedAt}}' 2>/dev/null)"
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
    build_args
    c_dim "Rebuilding ragclaw image (registry: ${REGISTRY_ARG:-<official>}, --no-cache) ..."
    compose build --progress=plain "${BUILD_ARGS[@]}" --no-cache ragclaw
    ;;
  reload)
    assert_docker
    if ! test_compose_available; then
      c_red "ERROR: docker-compose.yml missing or lacks 'ragclaw' service"
      exit 1
    fi
    c_cyan "=== Recreating backend container (no image rebuild) ==="
    compose up -d --force-recreate ragclaw || { c_red "ERROR: docker compose up failed"; exit 1; }
    if is_dev_mode; then
      c_dim "  Dev overlay: ./backend bind-mounted, uvicorn --reload active"
    fi
    wait_for_backend
    ;;
  logs)
    if test_docker_backend; then
      compose logs --tail=50 -f ragclaw
    else
      c_yellow "Backend not running in Docker mode"
    fi
    ;;
  *)
    c_yellow "Usage: bash bin/sh/backend.sh [FLAGS] [start|stop|reload|status|build|logs]"
    echo
    echo "  FLAGS:"
    echo "    --dev       Use docker-compose.dev.yml overlay (bind-mount + --reload)"
    echo "    --prod      Force base docker-compose.yml (default)"
    echo "    --registry <domain>  Docker base-image registry (default: docker.io)"
    echo "    --apt   <url>        apt mirror base URL (default: distro official)"
    echo "    --pypi  <url>        PyPI index URL (default: pypi.org)"
    echo "  Build sources are used verbatim; no reachability check is performed."
    echo
    echo "  start   Start backend (build + up, container mode)"
    echo "  stop    Stop backend"
    echo "  reload  Stop, recreate container (no image rebuild), start backend"
    echo "  status  Show running status (Docker)"
    echo "  build   Rebuild Docker image only (--no-cache)"
    echo "  logs    Tail Docker container logs"
    ;;
esac
