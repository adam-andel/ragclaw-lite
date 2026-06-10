# ---- Stage 1: Build Vue3 Frontend ----
FROM node:22-alpine AS frontend-builder
WORKDIR /app/frontend
COPY frontend/package.json frontend/package.json
# Use pnpm if available, fallback to npm
RUN corepack enable && pnpm install --frozen-lockfile || npm install
COPY frontend/ .
RUN npx vite build

# ---- Stage 2: Python Runtime ----
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install system dependencies for PyMuPDF
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgl1-mesa-glx libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY backend/pyproject.toml backend/pyproject.toml
RUN pip install --no-cache-dir -e backend/.

# Copy backend code
COPY backend/ backend/

# Copy frontend build output
COPY --from=frontend-builder /app/frontend/dist /app/frontend/dist

# Pre-download embedding model
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('BAAI/bge-small-zh-v1.5')"

# Create data directories
RUN mkdir -p /app/data/chroma /app/data/sqlite /app/data/uploads

EXPOSE 8000

CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
