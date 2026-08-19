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
# RAGClaw REPL MCP Server Control Script (macOS / Linux)
# Usage: bash bin/sh/mcp_repl.sh [start|stop|reload|status|build|logs]
#
# Container mode only: the REPL MCP server always runs as a Docker container
# (ragclaw-mcp-repl). Local Python venv execution is not supported — this
# project must run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down mcp-repl
#
# Languages: Python (always), Shell (enabled by default in container),
#           JavaScript (--enable-javascript, requires Node.js in image)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PORT=9200

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
# mcp-repl has no dev overlay of its own, but honoring the flag keeps its
# `compose` file list in sync with a dev-mode backend stack.
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
  grep -q 'mcp-repl:' "$COMPOSE_FILE"
}

test_docker_repl() {
  test_docker || return 1
  [ -n "$(docker ps -q -f "name=$(proj_name)-mcp-repl" 2>/dev/null)" ]
}

# ---- actions ----
start_docker_repl() {
  assert_docker
  c_cyan "=== REPL MCP Server (Docker :$PORT) ==="
  if test_docker_repl; then
    c_yellow "Already running on :$PORT (Docker mode)"
    return 0
  fi
  if ! test_compose_available; then
    c_red "ERROR: docker-compose.yml missing or lacks mcp-repl service"
    return 1
  fi
  build_args
  c_cyan "=== Building (registry: ${REGISTRY_ARG:-<official>}, apt: ${APT_MIRROR:-<official>}, pypi: ${PYPI_MIRROR:-<official>}) ==="
  compose build --progress=plain "${BUILD_ARGS[@]}" mcp-repl || { c_red "ERROR: build failed"; return 1; }
  echo
  c_cyan "=== Starting container ==="
  # ragclaw-egress owns a fixed internal IP (172.30.0.2) on $(proj_name)_ragclaw-internal.
  # A stale, non-running egress container (or a stuck IPAM lease left behind
  # after a prior `down`) can keep that IP occupied and make `up` fail with
  # "Address already in use". Clean both first.
  repair_egress_network
  if ! compose up -d mcp-repl ragclaw-egress; then
    # Second chance: the fixed egress IP is likely still leased on the
    # $(proj_name)_ragclaw-internal network. Tear the network down (releasing the IPAM
    # lease) and retry the bring-up once.
    c_yellow "  First attempt failed; releasing $(proj_name)_ragclaw-internal network lease and retrying..."
    repair_egress_network force
    if ! compose up -d mcp-repl ragclaw-egress; then
      c_red "ERROR: docker compose up failed"
      c_yellow "       The fixed egress IP (172.30.0.2) is still leased on the"
      c_yellow "       $(proj_name)_ragclaw-internal network and could not be auto-recovered."
      c_yellow "       Run these manually, then start again:"
      c_yellow "         docker compose -f docker-compose.yml down"
      c_yellow "         docker network rm $(proj_name)_ragclaw-internal"
      c_yellow "         docker compose -f docker-compose.yml up -d"
      return 1
    fi
  fi

  sleep 3
  if test_docker_repl; then
    c_green "REPL server started (Docker)"
    c_dim "  Endpoint: http://127.0.0.1:$PORT/mcp"
    c_dim "  Workspace: persistent volume ragclaw_workspace (survives restart)"
    c_dim "  Mode: Docker container (ragclaw-mcp-repl)"
    c_dim "  Resources: memory=896M, cpus=2"
    if test_docker_egress; then
      c_dim "  Egress broker: running (ragclaw-egress)"
    else
      c_yellow "  Egress broker: NOT running (ragclaw-egress)"
    fi
  else
    c_yellow "WARNING: Container not responding, check: docker logs $(proj_name)-mcp-repl"
  fi
}

stop_docker_repl() {
  c_cyan "=== Stopping REPL server + egress broker (Docker) ==="
  if test_docker_repl; then
    compose stop mcp-repl && c_green "REPL server stopped (Docker)"
  else
    c_yellow "REPL server not running (Docker)"
  fi
  if test_docker_egress; then
    compose stop ragclaw-egress && c_green "Egress broker stopped (Docker)"
  else
    c_yellow "Egress broker not running (Docker)"
  fi
}

show_status() {
  c_cyan "=== Python REPL MCP Server Status ==="
  c_dim "  Port: $PORT"
  c_cyan "  Mode: Docker container (container mode only)"
  if test_docker_repl; then
    c_green "  Status: running (ragclaw-mcp-repl)"
    local since
    since="$(docker inspect "$(proj_name)-mcp-repl" --format '{{.State.StartedAt}}' 2>/dev/null)"
    [ -n "$since" ] && c_dim "  Since:  $since"
  else
    c_red "  Status: REPL server NOT running"
  fi
  if test_docker_egress; then
    c_green "  Egress broker: running (ragclaw-egress)"
    local egress_since
    egress_since="$(docker inspect ragclaw-egress --format '{{.State.StartedAt}}' 2>/dev/null)"
    [ -n "$egress_since" ] && c_dim "    Since:  $egress_since"
  else
    c_yellow "  Egress broker: NOT running"
  fi
  if ! test_docker_repl && ! test_docker_egress; then
    if test_docker; then
      c_yellow "  Docker: available (nothing running)"
    else
      c_yellow "  Docker: not installed"
    fi
  fi
}

# ---- dispatch ----
ACTION="${1:-start}"
case "$ACTION" in
  start)
    start_docker_repl
    ;;
  stop)
    stop_docker_repl
    ;;
  status)
    show_status
    ;;
  build)
    assert_docker
    build_args
    c_dim "Rebuilding mcp-repl image (registry: ${REGISTRY_ARG:-<official>}, --no-cache) ..."
    compose build --progress=plain "${BUILD_ARGS[@]}" --no-cache mcp-repl
    ;;
  reload)
    assert_docker
    if ! test_compose_available; then
      c_red "ERROR: docker-compose.yml missing or lacks mcp-repl service"
      exit 1
    fi
    test_docker_repl && stop_docker_repl
    build_args
    c_cyan "=== Building (registry: ${REGISTRY_ARG:-<official>}, apt: ${APT_MIRROR:-<official>}, pypi: ${PYPI_MIRROR:-<official>}) ==="
    compose build --progress=plain "${BUILD_ARGS[@]}" mcp-repl || { c_red "ERROR: build failed"; exit 1; }
    echo
    c_cyan "=== Starting container ==="
    repair_egress_network
    if ! compose up -d mcp-repl ragclaw-egress; then
      c_yellow "  First attempt failed; releasing $(proj_name)_ragclaw-internal network lease and retrying..."
      repair_egress_network force
      if ! compose up -d mcp-repl ragclaw-egress; then
        c_red "ERROR: docker compose up failed"
        c_yellow "       The fixed egress IP (172.30.0.2) is still leased on the"
        c_yellow "       $(proj_name)_ragclaw-internal network. Run: docker compose down ; docker network rm $(proj_name)_ragclaw-internal ; docker compose up -d"
        exit 1
      fi
    fi
    sleep 3
    if test_docker_repl; then
      c_green "REPL server reloaded (Docker)"
      c_dim "  Endpoint: http://127.0.0.1:$PORT/mcp"
      c_dim "  Workspace: persistent volume ragclaw_workspace (survives restart)"
      c_dim "  Mode: Docker container (ragclaw-mcp-repl)"
      c_dim "  Resources: memory=896M, cpus=2"
    else
      c_yellow "WARNING: Container not responding, check: docker logs $(proj_name)-mcp-repl"
    fi
    ;;
  logs)
    if test_docker_repl; then
      compose logs --tail=50 -f ragclaw-mcp-repl
    else
      c_yellow "REPL server not running in Docker mode"
    fi
    ;;
  *)
    c_yellow "Usage: bash bin/sh/mcp_repl.sh [FLAGS] [start|stop|reload|status|build|logs]"
    echo
    echo "  FLAGS:"
    echo "    --dev       Use docker-compose.dev.yml overlay (harmless for mcp-repl;"
    echo "                keeps compose file list in sync with the dev backend stack)"
    echo "    --prod      Force base docker-compose.yml (default)"
    echo "    --registry <domain>  Docker base-image registry (default: docker.io)"
    echo "    --apt   <url>        apt mirror base URL (default: distro official)"
    echo "    --pypi  <url>        PyPI index URL (default: pypi.org)"
    echo "  Build sources are used verbatim; no reachability check is performed."
    echo
    echo "  start   Start REPL server (build + up, container mode)"
    echo "  stop    Stop REPL server"
    echo "  reload  Stop, rebuild image (uses cache), and start REPL server"
    echo "  status  Show running status"
    echo "  build   Rebuild Docker image only (--no-cache)"
    echo "  logs    Tail Docker container logs"
    ;;
esac
