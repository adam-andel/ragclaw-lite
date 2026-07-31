#!/bin/sh
# frontend/docker-entrypoint.dev.sh
#
# Dev-only entrypoint for the Vite HMR container (frontend-dev).
#
# The baked lockfile (/opt/frontend-lock.yaml) and baked store (/opt/pnpm-store)
# both came from the SAME `pnpm install` during image build, so they are always
# consistent. We copy the baked lockfile over whatever the host bind-mount brings
# in, then install strictly offline from the baked store.
# --offline is the only hard guard: pnpm never touches the network. pnpm's
# default (non-frozen) behaviour re-resolves if package.json drifted from the
# lockfile; --offline then either finds the packages in the store (works) or
# fails loudly — the error below tells you to rebuild the image.
set -eu

APP_DIR=/app/frontend
NODE_MODULES="$APP_DIR/node_modules"
STORE_DIR=/opt/pnpm-store
LOCKFILE_BAKED=/opt/frontend-lock.yaml

echo "[dev-entrypoint] syncing lockfile from baked copy..."
cp "$LOCKFILE_BAKED" "$APP_DIR/pnpm-lock.yaml"

echo "[dev-entrypoint] installing deps from baked store (offline)..."
rm -rf "$NODE_MODULES"

if ! pnpm install --offline --store-dir "$STORE_DIR"; then
  cat <<'EOF'

============================================================
 OFFLINE INSTALL FAILED
============================================================
 The baked pnpm store (/opt/pnpm-store) does not contain the
 packages required by the current package.json.

 Likely cause: you added/changed dependencies in package.json
 but did NOT rebuild the frontend-dev image.

 Fix: rebuild to refresh the baked store.
   ->  bash bin/sh/start.sh --dev reload
      (or press [3] in the main menu)

 If the error persists after a rebuild:
   ->  docker compose -f docker-compose.yml \
         -f docker-compose.dev.yml build --no-cache frontend-dev
============================================================

EOF
  exit 1
fi

echo "[dev-entrypoint] deps synced."
exec pnpm dev --host 0.0.0.0
