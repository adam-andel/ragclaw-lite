#!/bin/sh
# frontend/docker-entrypoint.dev.sh
#
# Dev-only entrypoint for the Vite HMR container (frontend-dev).
#
# node_modules lives in ./frontend/node_modules on the HOST — bind-mounted into
# the container at /app/frontend, written here by this entrypoint. It is NOT
# refreshed by a build when package.json changes, so a bare `reload` could
# leave the running Vite server unable to resolve a newly added dep (e.g.
# cronstrue).
#
# Instead of requiring a manual reset, this entrypoint self-heals by
# ALWAYS re-running `pnpm install --force` at startup, reusing the baked pnpm
# store at /opt/pnpm-store for speed (reuses baked store, no download). We deliberately
# do NOT gate this behind a "deps up-to-date" marker: a stale marker can lie
# after a half-failed install and leave the container stuck skipping forever.
# This makes adding a dependency safe: edit package.json, then
# `bash bin/sh/start.sh --dev reload` and the container reconciles itself.
set -eu

APP_DIR=/app/frontend
NODE_MODULES="$APP_DIR/node_modules"
STORE_DIR=/opt/pnpm-store

# Always reconcile node_modules against package.json/lockfile. node_modules lives
# on the host bind-mount, but that copy (or a prior half-failed install that
# wrote a stale checksum) can drift out of sync with package.json — e.g.
# cronstrue added but never actually linked. Re-syncing from the baked store is
# fast (reuses baked store, no download), so we ALWAYS do it instead of
# trusting an up-to-date marker, which can lie after a failed install and leave
# the container stuck skipping forever. `--force` also skips pnpm's interactive
# "remove and reinstall from scratch" prompt, which would otherwise hang with no TTY.
if [ ! -x "$NODE_MODULES/.bin/vite" ]; then
  echo "[dev-entrypoint] node_modules missing — installing deps..."
else
  echo "[dev-entrypoint] reconciling node_modules against package.json (fast re-link from store)..."
fi
if ! pnpm install --force --prefer-offline --store-dir "$STORE_DIR"; then
  echo "[dev-entrypoint] offline install failed, retrying online..."
  pnpm install --force --store-dir "$STORE_DIR"
fi
echo "[dev-entrypoint] deps synced."

exec pnpm dev --host 0.0.0.0
