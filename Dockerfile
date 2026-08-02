# ===== Stage 1: Build Vue3 Frontend =====
# REGISTRY build arg: set to a China mirror domain when docker.io is unreachable
#   docker compose build --build-arg REGISTRY=docker.m.daocloud.io
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/node:22-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package manifests (package.json + committed lockfile) so the frozen
# install resolves exactly from the pinned lockfile, not from semver ranges.
COPY frontend/package.json frontend/pnpm-lock.yaml ./

# BuildKit cache mount keeps the downloaded pnpm store across rebuilds — even when
# this layer is invalidated (e.g. frontend package.json changed) pnpm reuses cached
# tarballs instead of re-downloading. The mount id (pnpm-store) is scoped to this
# builder stage; the dev image no longer uses a cache mount (it bakes the store
# into an image layer instead), so this stage is now the only consumer.
RUN --mount=type=cache,id=pnpm-store,target=/root/.pnpm-store \
    corepack enable && pnpm install --frozen-lockfile --store-dir /root/.pnpm-store

# Copy source and build
COPY frontend/ .
RUN npx vite build

# ===== Stage 2: Python Runtime =====
FROM ${REGISTRY}/library/python:3.12-slim AS runtime

WORKDIR /app

# ── Layer 1: System deps (rarely changes) ──
# Note: libgl1-mesa-glx was removed in Debian 12 (Bookworm), use libgl1 instead
RUN (apt-get update && \
     apt-get install -y --no-install-recommends libgl1 libglib2.0-0) || \
    (sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
     apt-get update && \
     apt-get install -y --no-install-recommends libgl1 libglib2.0-0) \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 2: Prepare HuggingFace tooling (do NOT pre-download the ~2GB model) ──
# The BGE model is installed on demand from the Settings UI at runtime, so it is
# NOT baked into the image here. We only ensure huggingface_hub (used by the
# download endpoint) is present. HF_HOME points into the persistent /app/data
# volume so an installed model survives container restarts.
ENV HF_HOME=/app/data/hf_cache
# Disable the Xet CAS storage backend (default since huggingface_hub 0.26.0).
# It reconstructs large files via cas-server.xethub.hf.co and often fails with
# 401/503 on unstable networks / mirrors. Classic HTTP/LFS download is slower
# but far more reliable. Set before python starts so huggingface_hub reads it
# at import time (the flag is cached when the module is first imported).
ENV HF_HUB_DISABLE_XET=1
# Domestic PyPI mirror (overridable at build time via --build-arg PYPI_MIRROR=...);
# used only as a fallback when the official index is unreachable.
ARG PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
# Try the official PyPI first, fall back to the domestic mirror on failure
# (consistent with the apt source fallback rule above).
# BuildKit cache mount (id=pip-cache) keeps downloaded wheels across rebuilds —
# even when this layer is invalidated (e.g. base image bump) pip reuses cached
# wheels instead of re-downloading. The named id means the same cache is reused
# regardless of build-arg drift (REGISTRY switching between mirrors), avoiding
# anonymous orphan mounts that pile up reclaimable space.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install --quiet "huggingface_hub>=0.26.0" || \
    pip install --quiet --index-url ${PYPI_MIRROR} "huggingface_hub>=0.26.0"

# ── Layer 3: Python deps (changes more often than model, less than code) ──
# Single source of truth: backend/pyproject.toml. Avoids drift between
# pyproject.toml and a separate flat list here.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu

# Copy only pyproject.toml first to maximize layer caching of pip install.
COPY backend/pyproject.toml ./backend/
# A minimal placeholder so pip install . works without full source; we will
# overwrite with the real source in Layer 4.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    mkdir -p backend/app && touch backend/app/__init__.py && \
    (pip install ./backend || \
     pip install --index-url ${PYPI_MIRROR} ./backend)

# ── Layer 4: Backend code (most frequent changes) ──
COPY backend/ backend/

# ── Layer 4.5: Generate a fully-pinned lockfile (mirrors frontend pnpm-lock.yaml) ──
# `pip freeze` snapshots the EXACT resolved versions of every installed package
# (project deps + their transitive deps + torch/huggingface_hub installed above).
#
# Baked to /opt/backend-requirements.lock — OUTSIDE /app/backend so the dev
# bind-mount (./backend -> /app/backend) can never shadow it. The dev entrypoint
# (docker-compose.dev.yml) copies it back onto /app/backend/requirements.lock at
# container start, which writes through the bind-mount to the HOST ./backend so the
# lock can be committed and reused — exactly how frontend/docker-entrypoint.dev.sh
# syncs /opt/frontend-lock.yaml -> ./frontend/pnpm-lock.yaml.
#
# To make builds cross-machine reproducible, COMMIT ./backend/requirements.lock and
# switch the `pip install ./backend` step (Layer 3) to
# `pip install -r backend/requirements.lock` (add
# --extra-index-url https://download.pytorch.org/whl/cpu so torch+cpu still
# resolves — PyPI/pip has no CPU-only torch wheel). Until then this step merely
# records the resolved versions each build as a reproducibility reference.
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip freeze > /opt/backend-requirements.lock && \
    cp /opt/backend-requirements.lock /app/backend/requirements.lock

# ── Layer 5: Frontend dist ──
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Layer 6: Data dirs + non-root user (rarely changes) ──
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads /app/data/hf_cache /app/tls && \
    useradd -m -s /bin/bash ragclaw && \
    chown -R ragclaw:ragclaw /app

USER ragclaw
ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
