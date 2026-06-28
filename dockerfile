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

# System deps for PyMuPDF (deb.debian.org → TUNA mirror fallback)
# Note: libgl1-mesa-glx was removed in Debian 12 (Bookworm), use libgl1 instead
RUN (apt-get update || \
     (sed -i 's|http://deb.debian.org|http://mirrors.tuna.tsinghua.edu.cn|g' /etc/apt/sources.list.d/debian.sources && \
      apt-get update)) \
    && apt-get install -y --no-install-recommends \
    libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps (CPU-only torch first to avoid nvidia CUDA bloat)
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
    pip install --no-cache-dir \
    fastapi uvicorn[standard] sqlalchemy aiosqlite chromadb \
    sentence-transformers pymupdf python-docx markdown \
    jieba rank-bm25 tiktoken httpx sse-starlette \
    pydantic-settings python-multipart

# Copy backend code
COPY backend/ backend/

# Copy frontend dist
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Pre-download BGE model (use HF mirror)
ENV HF_ENDPOINT=https://hf-mirror.com
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"

# Data dirs
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads

ENV PYTHONPATH=/app/backend
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
