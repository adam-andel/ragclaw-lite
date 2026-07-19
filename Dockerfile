# ===== Stage 1: Build Vue3 Frontend =====
# REGISTRY build arg: set to a China mirror domain when docker.io is unreachable
#   docker compose build --build-arg REGISTRY=docker.m.daocloud.io
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/node:22-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package files
COPY frontend/package.json ./

RUN corepack enable && pnpm install --no-frozen-lockfile

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
# Domestic PyPI mirror (overridable at build time via --build-arg PYPI_MIRROR=...);
# used only as a fallback when the official index is unreachable.
ARG PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple
# Try the official PyPI first, fall back to the domestic mirror on failure
# (consistent with the apt source fallback rule above).
# BuildKit cache mount (target=/root/.cache/pip) keeps downloaded wheels across
# rebuilds — even when this layer is invalidated (e.g. base image bump) pip
# reuses cached wheels instead of re-downloading. Wheels live outside the image.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --quiet huggingface_hub || \
    pip install --quiet --index-url ${PYPI_MIRROR} huggingface_hub

# ── Layer 3: Python deps (changes more often than model, less than code) ──
# Single source of truth: backend/pyproject.toml. Avoids drift between
# pyproject.toml and a separate flat list here.
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install torch --index-url https://download.pytorch.org/whl/cpu

# Copy only pyproject.toml first to maximize layer caching of pip install.
COPY backend/pyproject.toml ./backend/
# A minimal placeholder so pip install . works without full source; we will
# overwrite with the real source in Layer 4.
RUN --mount=type=cache,target=/root/.cache/pip \
    mkdir -p backend/app && touch backend/app/__init__.py && \
    (pip install ./backend || \
     pip install --index-url ${PYPI_MIRROR} ./backend)

# ── Layer 4: Backend code (most frequent changes) ──
COPY backend/ backend/

# ── Layer 5: Frontend dist ──
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Layer 6: Data dirs + non-root user (rarely changes) ──
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads /app/data/hf_cache && \
    useradd -m -s /bin/bash erag && \
    chown -R erag:erag /app

USER erag
ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
