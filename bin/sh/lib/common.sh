#!/usr/bin/env bash
# =====================================================================
# Shared helpers for bin/sh/*.sh — source AFTER setting:
#   SCRIPT_DIR, ROOT, PORT, COMPOSE_FILE (and optionally COMPOSE_DEV)
#
# Faithful bash port of the common logic that was duplicated across the
# bin/psl/*.ps1 scripts (Assert-Docker, Repair-EgressNetwork, etc.).
# =====================================================================

# ---- ANSI colors (use `echo -e`) ----
c_red()    { echo -e "\033[31m$*\033[0m"; }
c_yellow() { echo -e "\033[33m$*\033[0m"; }
c_cyan()   { echo -e "\033[36m$*\033[0m"; }
c_green()  { echo -e "\033[32m$*\033[0m"; }
c_dim()    { echo -e "\033[90m$*\033[0m"; }
c_magenta(){ echo -e "\033[35m$*\033[0m"; }

# ---- Docker availability ----
test_docker() {
  command -v docker >/dev/null 2>&1 && docker --version >/dev/null 2>&1
}

assert_docker() {
  if ! test_docker; then
    c_red "ERROR: Docker is not installed or not running."
    c_yellow "       This project runs in container mode only. Please install Docker."
    exit 1
  fi
}

# ---- compose() wrapper ----
# Honors an optional COMPOSE_DEV overlay (docker-compose.dev.yml) when the
# caller sets it. That file adds the dev experience: backend source bind-mounted
# with uvicorn --reload, plus a Vite `frontend-dev` HMR server on :5173.
# Dev mode is toggled per-script via the `--dev` CLI flag (or RAGCLAW_DEV=1 env).
compose() {
  if [ -n "${COMPOSE_DEV:-}" ]; then
    docker compose -f "$COMPOSE_FILE" -f "$COMPOSE_DEV" "$@"
  else
    docker compose -f "$COMPOSE_FILE" "$@"
  fi
}

# ---- Dev-mode helpers ----
# True when the dev overlay (COMPOSE_DEV) is active.
is_dev_mode() { [ -n "${COMPOSE_DEV:-}" ]; }

# Short label for banners: "dev" or "prod".
mode_label() { is_dev_mode && echo "dev" || echo "prod"; }

# ---- Free the fixed egress IP (172.30.0.2) on ragclaw-internal ----
# Without this, `up` can fail with "Address already in use". Pass "force"
# to ALSO tear down the network and release a stuck Docker IPAM lease.
repair_egress_network() {
  test_docker || return 0
  local egress_id running attached
  egress_id="$(docker ps -a -q -f name=ragclaw-egress 2>/dev/null)"
  if [ -n "$egress_id" ]; then
    running="$(docker ps -q -f name=ragclaw-egress 2>/dev/null)"
    if [ -z "$running" ]; then
      c_dim "  Removing stale ragclaw-egress container to free its fixed IP..."
      compose rm -f ragclaw-egress >/dev/null 2>&1
    fi
  fi
  [ "${1:-}" = "force" ] || return 0
  # The internal network is named by compose as {COMPOSE_PROJECT_NAME}_ragclaw-internal.
  # Resolve the project name the SAME way compose does (env > .env > directory
  # basename) so this works for any COMPOSE_PROJECT_NAME (ragclaw here, erag before).
  # Do NOT hardcode the bare "ragclaw-internal": compose prefixes it with the
  # project name, so a literal rm was a silent no-op and the 172.30.0.2 IPAM
  # lease was never released -> "Address already in use" on every retry.
  local proj="${COMPOSE_PROJECT_NAME:-}"
  if [ -z "$proj" ] && [ -f "$ROOT/.env" ]; then
    proj="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ROOT/.env" 2>/dev/null | head -n1 | cut -d= -f2-)"
  fi
  proj="${proj:-$(basename "$ROOT")}"
  local net="${proj}_ragclaw-internal"
  # Force mode frees the egress IP (172.30.0.2) IPAM lease by removing the
  # internal network. BUT the backend (ragclaw-lite) and mcp-repl are NORMAL
  # members of that network — removing the network requires all endpoints to be
  # detached first, and we must NOT `docker rm -f` them (that would take down the
  # live backend). So: delete ONLY the egress broker container, and merely
  # `disconnect` every other attached container so the network can be removed;
  # `docker compose up` then recreates the network and reconnects them.
  local attached name
  attached="$(docker network inspect "$net" --format '{{range $k,$v := .Containers}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null)"
  if [ -n "$attached" ]; then
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's@^/@@')"
      if [ "$name" = "ragclaw-egress" ] || [ "$id" = "$egress_id" ]; then
        docker rm -f "$id" >/dev/null 2>&1                       # the broken broker — safe to delete
      else
        docker network disconnect -f "$net" "$id" >/dev/null 2>&1 # keep the container, just detach
      fi
    done <<< "$attached"
  fi
  docker network rm "$net" >/dev/null 2>&1
  c_dim "  Released $net network IPAM lease; will recreate on up."
}

# ---- Egress broker running? ----
test_docker_egress() {
  test_docker || return 1
  [ -n "$(docker ps -q -f name=ragclaw-egress 2>/dev/null)" ]
}

# ---- Wait for backend /api/health (model load may take ~30s) ----
wait_for_backend() {
  echo -n "Waiting for backend (loading model, may take ~30s)..."
  local i code
  for i in $(seq 1 90); do
    sleep 1
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "http://127.0.0.1:${PORT:-8000}/api/health" 2>/dev/null)"
    if [ "$code" = "200" ]; then
      echo " OK"
      return 0
    fi
    [ $((i % 5)) -eq 0 ] && echo -n "."
  done
  echo " timeout!"
  c_dim "  Check manually: docker logs ragclaw-lite"
  return 1
}
