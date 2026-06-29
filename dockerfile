# ===== Stage 1: Build Vue3 Frontend =====
FROM node:22-alpine AS frontend-builder

WORKDIR /app/frontend

# Copy package files
COPY frontend/package.json ./

RUN corepack enable && pnpm install --no-frozen-lockfile

# Copy source and build
COPY frontend/ .
RUN npx vite build

# ===== Stage 2: Python Runtime =====
FROM python:3.12-slim AS runtime

WORKDIR /app

# ── Layer 1: System deps (rarely changes) ──
# Note: libgl1-mesa-glx was removed in Debian 12 (Bookworm), use libgl1 instead
RUN (apt-get update && \
     apt-get install -y --no-install-recommends libgl1 libglib2.0-0) || \
    (sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
     apt-get update && \
     apt-get install -y --no-install-recommends libgl1 libglib2.0-0) \
    && rm -rf /var/lib/apt/lists/*

# ── Layer 2: Pre-download BGE model (almost never changes, ~2GB) ──
# Uses huggingface_hub (lightweight) so dependency changes won't re-download the model.
# Must be BEFORE Layer 3 (pip install) to maximize cache reuse.
ENV HF_HOME=/app/.cache/huggingface \
    HF_ENDPOINT=https://hf-mirror.com
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir --quiet huggingface_hub && \
    python -c "from huggingface_hub import snapshot_download; snapshot_download('BAAI/bge-small-zh-v1.5')"

# ── Layer 3: Python deps (changes more often than model, less than code) ──
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir \
    fastapi uvicorn[standard] sqlalchemy aiosqlite chromadb \
    sentence-transformers pymupdf python-docx markdown \
    jieba rank-bm25 tiktoken httpx sse-starlette \
    pydantic-settings python-multipart python-jose[cryptography] \
    passlib[bcrypt] langgraph mem0ai

# ── Layer 4: Backend code (most frequent changes) ──
COPY backend/ backend/

# ── Layer 5: Frontend dist ──
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# ── Layer 6: Data dirs + non-root user (rarely changes) ──
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads && \
    useradd -m -s /bin/bash erag && \
    chown -R erag:erag /app

USER erag
ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
