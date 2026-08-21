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
# with uvicorn --reload, plus a Vite `frontend-dev` HMR server (host port via
# RAGCLAW_FRONTEND_PORT, default 5173).
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

# Resolve the Compose project name the SAME way `docker compose` does:
#   $COMPOSE_PROJECT_NAME  >  .env (COMPOSE_PROJECT_NAME=)  >  directory basename.
# Used to derive project-scoped container names (e.g. "{proj}ragclaw-egress") so the
# helper scripts work for ANY instance, not just the default "ragclaw" project.
proj_name() {
  local p="${COMPOSE_PROJECT_NAME:-}"
  if [ -z "$p" ] && [ -f "$ROOT/.env" ]; then
    p="$(grep -E '^COMPOSE_PROJECT_NAME=' "$ROOT/.env" 2>/dev/null | head -n1 | cut -d= -f2-)"
  fi
  echo "${p:-$(basename "$ROOT")}"
}

# ---- Free the fixed egress IP on the internal network ----
# Without this, `up` can fail with "Address already in use". Pass "force"
# to ALSO tear down the network and release a stuck Docker IPAM lease.
repair_egress_network() {
  test_docker || return 0
  local proj egress_name egress_id running attached name net
  proj="$(proj_name)"
  egress_name="${proj}ragclaw-egress"
  egress_id="$(docker ps -a -q -f "name=${egress_name}" 2>/dev/null)"
  if [ -n "$egress_id" ]; then
    running="$(docker ps -q -f "name=${egress_name}" 2>/dev/null)"
    if [ -z "$running" ]; then
      c_dim "  Removing stale ${egress_name} container to free its fixed IP..."
      # `compose rm` operates on the SERVICE name (ragclaw-egress), which is
      # project-scoped automatically — correct for any COMPOSE_PROJECT_NAME.
      compose rm -f ragclaw-egress >/dev/null 2>&1
    fi
  fi
  [ "${1:-}" = "force" ] || return 0
  # The internal network is named by compose as {project}_ragclaw-internal and the
  # egress container as "{project}ragclaw-egress" (see compose container_name). Match by
  # the project-derived name — never a hardcoded "ragclaw-egress" — otherwise a
  # SECOND instance (e.g. COMPOSE_PROJECT_NAME=dev) would never be matched and its
  # stuck IPAM lease could not be released.
  net="${proj}_ragclaw-internal"
  # Force mode frees the egress IP IPAM lease by removing the internal network.
  # BUT the backend ({project}ragclaw-lite) and mcp-repl are NORMAL members of that
  # network — removing it requires all endpoints detached first, and we must NOT
  # `docker rm -f` them (that would take down the live backend). Delete ONLY the
  # egress broker; merely `disconnect` every other attached container so the
  # network can be removed; `docker compose up` recreates it and reconnects them.
  attached="$(docker network inspect "$net" --format '{{range $k,$v := .Containers}}{{$k}}{{"\n"}}{{end}}' 2>/dev/null)"
  if [ -n "$attached" ]; then
    while IFS= read -r id; do
      [ -n "$id" ] || continue
      name="$(docker inspect --format '{{.Name}}' "$id" 2>/dev/null | sed 's@^/@@')"
      if [ "$name" = "$egress_name" ] || [ "$id" = "$egress_id" ]; then
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
  [ -n "$(docker ps -q -f "name=$(proj_name)ragclaw-egress" 2>/dev/null)" ]
}

# ---- Wait for backend /api/health (model load may take ~30s) ----
wait_for_backend() {
  echo -n "Waiting for backend (loading model, may take ~30s)..."
  local i code
  for i in $(seq 1 90); do
    sleep 1
    # Prefer HEALTH_URL (set by start.sh to the real entry's HTTP URL: nginx
    # in prod, Vite frontend in dev) since the backend is no longer published
    # to the host. Fall back to 127.0.0.1:8000 for legacy/local setups.
    local url="${HEALTH_URL:-http://127.0.0.1:${PORT:-8000}}/api/health"
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 2 "$url" 2>/dev/null)"
    if [ "$code" = "200" ]; then
      echo " OK"
      return 0
    fi
    [ $((i % 5)) -eq 0 ] && echo -n "."
  done
  echo " timeout!"
  c_dim "  Check manually: docker logs $(proj_name)ragclaw-lite"
  return 1
}
