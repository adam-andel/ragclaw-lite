#!/usr/bin/env bash
# RAGClaw REPL MCP Server Control Script (WSL / Linux)
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
source "$SCRIPT_DIR/lib/mirror.sh"

# Dev-mode toggle: optional leading --dev / --prod flag (env RAGCLAW_DEV default).
# mcp-repl has no dev overlay of its own, but honoring the flag keeps its
# `compose` file list in sync with a dev-mode backend stack.
RAGCLAW_DEV="${RAGCLAW_DEV:-0}"
case "${1:-}" in
  --dev)  RAGCLAW_DEV=1; shift ;;
  --prod) RAGCLAW_DEV=0; shift ;;
esac
COMPOSE_FILE="$ROOT/docker-compose.yml"
if [ "$RAGCLAW_DEV" = "1" ] && [ -f "$ROOT/docker-compose.dev.yml" ]; then
  COMPOSE_DEV="$ROOT/docker-compose.dev.yml"
fi

REQUIRED_IMAGES=("library/python:3.12-slim")

# ---- helpers ----
test_compose_available() {
  [ -f "$COMPOSE_FILE" ] || return 1
  grep -q 'mcp-repl:' "$COMPOSE_FILE"
}

test_docker_repl() {
  test_docker || return 1
  [ -n "$(docker ps -q -f name=ragclaw-mcp-repl 2>/dev/null)" ]
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
  local build_mirror
  build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
  if [ -z "$build_mirror" ]; then
    c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
    return 1
  fi
  c_cyan "=== Building (registry: $build_mirror) ==="
  compose build --build-arg REGISTRY="$build_mirror" mcp-repl || { c_red "ERROR: build failed"; return 1; }
  echo
  c_cyan "=== Starting container ==="
  # ragclaw-egress owns a fixed internal IP (172.30.0.2) on ragclaw_ragclaw-internal.
  # A stale, non-running egress container (or a stuck IPAM lease left behind
  # after a prior `down`) can keep that IP occupied and make `up` fail with
  # "Address already in use". Clean both first.
  repair_egress_network
  if ! compose up -d mcp-repl ragclaw-egress; then
    # Second chance: the fixed egress IP is likely still leased on the
    # ragclaw_ragclaw-internal network. Tear the network down (releasing the IPAM
    # lease) and retry the bring-up once.
    c_yellow "  First attempt failed; releasing ragclaw_ragclaw-internal network lease and retrying..."
    repair_egress_network force
    if ! compose up -d mcp-repl ragclaw-egress; then
      c_red "ERROR: docker compose up failed"
      c_yellow "       The fixed egress IP (172.30.0.2) is still leased on the"
      c_yellow "       ragclaw_ragclaw-internal network and could not be auto-recovered."
      c_yellow "       Run these manually, then start again:"
      c_yellow "         docker compose -f docker-compose.yml down"
      c_yellow "         docker network rm ragclaw_ragclaw-internal"
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
    c_yellow "WARNING: Container not responding, check: docker logs ragclaw-mcp-repl"
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
    since="$(docker inspect ragclaw-mcp-repl --format '{{.State.StartedAt}}' 2>/dev/null)"
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
    build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$build_mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    c_dim "Rebuilding mcp-repl image (registry: $build_mirror, --no-cache) ..."
    compose build --build-arg REGISTRY="$build_mirror" --no-cache mcp-repl
    ;;
  reload)
    assert_docker
    if ! test_compose_available; then
      c_red "ERROR: docker-compose.yml missing or lacks mcp-repl service"
      exit 1
    fi
    test_docker_repl && stop_docker_repl
    build_mirror="$(get_working_mirror_domain "${REQUIRED_IMAGES[@]}")"
    if [ -z "$build_mirror" ]; then
      c_red "ERROR: no working mirror available (all registries rate-limited or unreachable)"
      exit 1
    fi
    c_cyan "=== Building (registry: $build_mirror) ==="
    compose build --build-arg REGISTRY="$build_mirror" mcp-repl || { c_red "ERROR: build failed"; exit 1; }
    echo
    c_cyan "=== Starting container ==="
    repair_egress_network
    if ! compose up -d mcp-repl ragclaw-egress; then
      c_yellow "  First attempt failed; releasing ragclaw_ragclaw-internal network lease and retrying..."
      repair_egress_network force
      if ! compose up -d mcp-repl ragclaw-egress; then
        c_red "ERROR: docker compose up failed"
        c_yellow "       The fixed egress IP (172.30.0.2) is still leased on the"
        c_yellow "       ragclaw_ragclaw-internal network. Run: docker compose down ; docker network rm ragclaw_ragclaw-internal ; docker compose up -d"
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
      c_yellow "WARNING: Container not responding, check: docker logs ragclaw-mcp-repl"
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
    c_yellow "Usage: bash bin/sh/mcp_repl.sh [--dev|--prod] [start|stop|reload|status|build|logs]"
    echo
    echo "  --dev   Use docker-compose.dev.yml overlay (harmless for mcp-repl;"
    echo "          keeps compose file list in sync with the dev backend stack)"
    echo "  --prod  Force base docker-compose.yml (default)"
    echo
    echo "  start   Start REPL server (build + up, container mode)"
    echo "  stop    Stop REPL server"
    echo "  reload  Stop, rebuild image (uses cache), and start REPL server"
    echo "  status  Show running status"
    echo "  build   Rebuild Docker image only (--no-cache)"
    echo "  logs    Tail Docker container logs"
    ;;
esac
