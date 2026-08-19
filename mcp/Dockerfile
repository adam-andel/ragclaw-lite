# REPL MCP Server — Docker image (Python + Shell + JavaScript sandbox)
# Isolated execution environment for LLM-generated code
# REGISTRY build arg: set to a mirror domain when docker.io is unreachable.
#   docker compose build --build-arg REGISTRY=docker.xuanyuan.me mcp-repl
# APT_MIRROR build arg: Debian apt mirror HOST (no scheme). Pass a bare host
# name, e.g. mirrors.tuna.tsinghua.edu.cn (do NOT include https:// here).
#   docker compose build --build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn mcp-repl
# PYPI_MIRROR build arg: PyPI index URL. Empty -> official pypi.org (no
# --index-url passed, pip uses its default). Set to a mirror to override.
#   docker compose build --build-arg PYPI_MIRROR=https://pypi.tuna.tsinghua.edu.cn/simple mcp-repl
ARG REGISTRY=docker.io

FROM ${REGISTRY}/library/python:3.12-slim

# Declare source ARGs inside the build stage so they are visible to RUN below.
# ARGs are NOT visible across `FROM` boundaries, so they MUST be declared here
# (after FROM) with their defaults; values can still be overridden via
# --build-arg at build time.
ARG APT_MIRROR=""
ARG PYPI_MIRROR=""

# Create non-root user (least privilege)
RUN useradd -m -s /bin/bash mcpuser

WORKDIR /app

# Install Node.js LTS for JavaScript execution support
# When APT_MIRROR is set, rewrite the deb.debian.org host to the mirror. The
# scheme in debian.sources is preserved; any scheme the caller may have prepended
# to APT_MIRROR is stripped first so both "host" and "https://host" inputs work.
RUN if [ -n "$APT_MIRROR" ]; then \
      MIRROR_HOST=$(echo "$APT_MIRROR" | sed -E 's#^https?://##'); \
      sed -i -E "s#https?://deb\.debian\.org#https://$MIRROR_HOST#g" /etc/apt/sources.list.d/debian.sources; \
    fi && \
    apt-get update && \
    apt-get install -y --no-install-recommends \
        nodejs \
        npm \
        jq \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies into a shared site from the PINNED lockfile.
# requirements.lock is produced by `pip freeze` (fully resolved, incl. transitives)
# and committed for reproducible builds; requirements.txt stays the floating
# "intent" source. Regenerate the lock after editing requirements.txt:
#   pip install -r requirements.txt && pip freeze > requirements.lock
# BuildKit cache mount (id=pip-cache) keeps pip's HTTP/wheel cache across builds
# for faster rebuilds. The named id is shared with the dev image's pip cache
# (Dockerfile.dev) so the same wheels are reused and no orphan mounts accumulate
# when REGISTRY drifts.
COPY requirements.lock .
RUN --mount=type=cache,id=pip-cache,target=/root/.cache/pip \
    pip install -r requirements.lock ${PYPI_MIRROR:+--index-url ${PYPI_MIRROR}}

# Copy server code (kept minimal — no venv, no tests)
COPY repl_mcp_server.py .
COPY python_repl_mcp_server.py .
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
