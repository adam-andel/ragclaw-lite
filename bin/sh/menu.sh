#!/usr/bin/env bash
# ERAG Control Menu (WSL / Linux)
# Usage: bash bin/sh/menu.sh
#
# Interactive equivalent of bin/psl/menu.bat. Loops until the user picks [0].

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

while true; do
  echo
  echo "  ==================================="
  echo "    EnterpriseRAG-Lite"
  echo "  ==================================="
  echo
  echo "    [1] Start All  (prod)"
  echo "    [2] Reload All (prod)"
  echo "    [3] Start All  (dev: HMR + --reload)"
  echo "    [4] Reload All (dev)"
  echo "    [5] Stop All"
  echo "    [6] Status"
  echo "    [7] Backend Only (prod)"
  echo "    [8] Backend Only (dev)"
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
