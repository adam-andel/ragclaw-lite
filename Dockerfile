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
# APT_MIRROR defaults to the Debian official source when the build arg is unset.
# Pass a BARE HOST (no scheme), e.g. --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn
# (do NOT include https:// — the scheme in debian.sources is preserved).
# PyPI index URL. Empty -> official pypi.org (no --index-url passed, pip uses
# its default). Set to a mirror (e.g. https://pypi.tuna.tsinghua.edu.cn/simple)
# to override. TORCH_INDEX is the torch-only CPU-wheel index, always passed as a
# SUPPLEMENTARY --extra-index-url (torch lives ONLY there).
FROM ${REGISTRY}/library/python:3.12-slim AS runtime

# Declare source ARGs inside the build stage so they are visible to RUN below.
# ARGs are NOT visible across `FROM` boundaries, so they MUST be declared here
# (after FROM) with their defaults; values can still be overridden via
# --build-arg at build time.
ARG APT_MIRROR=""
ARG PYPI_MIRROR=""
ARG TORCH_INDEX=https://download.pytorch.org/whl/cpu

WORKDIR /app

# ── Layer 1: System deps (rarely changes) ──
# Note: libgl1-mesa-glx was removed in Debian 12 (Bookworm), use libgl1 instead
# When APT_MIRROR is set, rewrite the deb.debian.org host to the mirror. The
# scheme in debian.sources is preserved; any scheme the caller may have prepended
# to APT_MIRROR is stripped first so both "host" and "https://host" inputs work.
RUN if [ -n "$APT_MIRROR" ]; then \
      MIRROR_HOST=$(echo "$APT_MIRROR" | sed -E 's#^https?://##'); \
      sed -i -E "s#https?://deb\.debian\.org#https://$MIRROR_HOST#g" /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 2: Build config & mirror fallback (no installs here) ──
# HF_HOME points into the persistent /app/data volume so a BGE model installed
# on demand from the Settings UI at runtime survives container restarts.
ENV HF_HOME=/app/data/hf_cache
# Disable the Xet CAS storage backend (default since huggingface_hub 0.26.0).
# It reconstructs large files via cas-server.xethub.hf.co and often fails with
# 401/503 on unstable networks / mirrors. Classic HTTP/LFS download is slower
# but far more reliable. Set before python starts so huggingface_hub reads it
# at import time (the flag is cached when the module is first imported).
ENV HF_HUB_DISABLE_XET=1

# ── Layer 3: Python deps — fully pinned from requirements.lock ──
# Mirror of frontend's `--frozen-lockfile`: every direct + transitive dep version
# is frozen, so rebuilds never re-resolve against PyPI and never silently drift.
# torch (+cpu wheel) lives ONLY on the pytorch index, so that is passed as an
# EXTRA index — NOT --index-url, which would shadow PyPI for the other 165 pkgs.
# BuildKit cache mount (id=pip-cache) still keeps wheels across rebuilds.
COPY backend/requirements.lock ./backend/
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install -r backend/requirements.lock \
        ${PYPI_MIRROR:+--index-url ${PYPI_MIRROR}} \
        --extra-index-url ${TORCH_INDEX}

# ── Layer 3b: Pre-bake tiktoken BPE so startup never blocks on a flaky egress ──
# tiktoken downloads cl100k_base.tiktoken (~1.7MB) on first use. Over an unstable
# egress proxy this intermittently breaks mid-transfer (IncompleteRead) and crashes
# app startup (the import is at module level in chunker.py / token_count.py). Bake it
# into a read-only image cache and point TIKTOKEN_CACHE_DIR at it so startup reads
# locally. Retried because the upstream blob download itself is flaky; a hard failure
# here fails the build loudly instead of shipping a silently-broken image.
ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken_cache
RUN mkdir -p "$TIKTOKEN_CACHE_DIR" && \
    python3 - <<'PY'
import sys, time
for attempt in range(1, 6):
    try:
        import tiktoken
        tiktoken.get_encoding("cl100k_base")
        break
    except Exception as e:
        if attempt == 5:
            print(f"[tiktoken] prebake failed after 5 attempts: {e!r}", file=sys.stderr)
            raise
        time.sleep(2)
PY

# ── Layer 4: Backend code (most frequent changes) ──
COPY backend/ backend/

# ── Layer 5: Frontend dist ──
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Layer 6: Data dirs + non-root user (rarely changes) ──
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads /app/data/hf_cache /app/tls && \
    useradd -m -s /bin/bash ragclaw && \
    chown -R ragclaw:ragclaw /app

USER ragclaw
ENV PYTHONPATH=/app/backend
EXPOSE 8000

# ── Hard constraint: SINGLE uvicorn worker (--workers 1) ──
# RAGClaw relies on process-local singletons that MUST stay in ONE process:
#   * the asyncio LLM concurrency semaphore (llm_semaphore.py)
#   * the in-memory BM25 index (bm25_index.py)
#   * the in-process answer cache + functools.lru_cache
#   * Chroma PersistentClient opened per-process on the SAME disk path
# Running >1 worker forks N independent copies of all of the above — the
# semaphore would be N× too large, BM25/Chroma/cache would be fragmented and
# never shared, silently defeating their purpose. There is intentionally NO
# RAGCLAW_MAX_WORKERS knob: do NOT add --workers N here. Scale horizontally
# (more containers) instead of vertically (more workers), which keeps the
# singletons correct per instance.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
