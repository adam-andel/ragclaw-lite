#!/usr/bin/env bash
# RAGClaw Control Menu (macOS / Linux)
# Usage: bash bin/sh/menu.sh
#
# Interactive equivalent of bin/psl/menu.bat. Loops until the user picks [0].

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while true; do
  echo
  echo "  ==================================="
  echo "    RAGClaw-Lite"
  echo "  ==================================="
  echo
  echo "    [1] Start All  (prod)"
  echo "        -> compose build (ragclaw mcp-repl ragclaw-egress nginx) + compose up -d"
  echo "    [2] Reload All (prod)"
  echo "        -> compose up -d --force-recreate   (containers only, NO image rebuild)"
  echo "    [3] Start All  (dev : HMR + --reload)"
  echo "        -> compose build (all + frontend-dev) + compose up -d --force-recreate  [dev overlay]"
  echo "    [4] Reload All (dev)"
  echo "        -> compose up -d --force-recreate   (containers only, NO image rebuild)  [dev overlay]"
  echo "    [5] Stop All"
  echo "        -> compose stop   (pause all containers; images & volumes kept)"
  echo "    [6] Status"
  echo "        -> compose ps   (services / published ports / health)"
  echo "    [7] Backend Only (prod)"
  echo "        -> compose build ragclaw + compose up -d ragclaw   (backend service only)"
  echo "    [8] Backend Only (dev)"
  echo "        -> compose build ragclaw + compose up -d ragclaw   (dev: bind-mount + uvicorn --reload)"
  echo "    [0] Exit"
  echo
  printf "Choose: "
  read -r choice
  case "$choice" in
    1) bash "$SCRIPT_DIR/start.sh" start ;;
    2) bash "$SCRIPT_DIR/start.sh" reload ;;
    3) bash "$SCRIPT_DIR/start.sh" --dev start ;;
    4) bash "$SCRIPT_DIR/start.sh" --dev reload ;;
    5) bash "$SCRIPT_DIR/start.sh" stop ;;
    6) bash "$SCRIPT_DIR/start.sh" status ;;
    7) bash "$SCRIPT_DIR/backend.sh" start ;;
    8) bash "$SCRIPT_DIR/backend.sh" --dev start ;;
    0) echo "Bye."; exit 0 ;;
    *) echo "Invalid choice." ;;
  esac
done
