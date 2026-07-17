# REPL MCP Server — Docker image (Python + Shell + JavaScript sandbox)
# Isolated execution environment for LLM-generated code
# REGISTRY build arg: set to a China mirror domain when docker.io is unreachable
#   docker build --build-arg REGISTRY=docker.xuanyuan.me ...
#   Or via compose: docker compose build --build-arg REGISTRY=docker.xuanyuan.me mcp-repl
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/python:3.12-slim

# Create non-root user (least privilege)
RUN useradd -m -s /bin/bash mcpuser

WORKDIR /app

# Install Node.js LTS for JavaScript execution support
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        nodejs \
        npm \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a shared site.
# BuildKit cache mount keeps pip's HTTP/wheel cache across builds for faster rebuilds.
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install -r requirements.txt || \
    (pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple && \
     pip install -r requirements.txt)

# Copy server code (kept minimal — no venv, no tests)
COPY repl_mcp_server.py .
COPY python_repl_mcp_server.py .
COPY egress_proxy.py .

COPY entrypoint.sh .
RUN sed -i 's/\r$//' entrypoint.sh && chmod +x entrypoint.sh

# Workspace directory for code execution output
RUN mkdir -p /app/workspace && chown mcpuser:mcpuser /app/workspace

# Disable .pyc bytecode cache (read-only filesystem)
ENV PYTHONDONTWRITEBYTECODE=1

USER mcpuser
# EXPOSE 9200

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=5s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:9200/health')" || exit 1

ENTRYPOINT ["./entrypoint.sh"]
CMD ["--port", "9200", \
     "--allow-dir", "/app/workspace", \
     "--no-network", \
     "--max-memory-mb", "512", \
     "--max-nproc", "64", \
     "--max-concurrent", "4", \
     "--enable-shell", \
     "--enable-javascript"]
