# =====================================================================
# Shared helpers for bin/psl/*.ps1 — dot-source AFTER setting $ComposeFile.
#
# Faithful PowerShell counterpart of bin/sh/lib/common.sh. Currently exposes
# the ragclaw published-port resolver so every script reports the REAL host port
# (inline RAGCLAW_PORT > .env RAGCLAW_PORT > random), not a hardcoded :8000.
# =====================================================================

# ---- Resolve the actual host port the ragclaw service is published on ----
# Mirrors bin/sh/lib/common.sh::ragclaw_published_port.
# Handles all three cases uniformly: inline RAGCLAW_PORT, .env RAGCLAW_PORT, or a
# random ephemeral port. Requires the container to be up (call after `up`).
# `docker compose port ragclaw 8000` prints e.g. "0.0.0.0:8000" or ":::8000";
# if it cannot be resolved yet (container not running), fall back to 8000.
function Get-RagclawPublishedPort {
    $out = docker compose -f $ComposeFile port ragclaw 8000 2>$null
    if ($out) {
        $port = ($out -split ':' | Select-Object -Last 1).Trim()
        if ($port -match '^\d+$') { return $port }
    }
    return 8000
}

# ---- Resolve the Compose project name the SAME way `docker compose` does ----
# ($COMPOSE_PROJECT_NAME > .env COMPOSE_PROJECT_NAME= > directory basename) and
# return the project-scoped container-name prefix, e.g. "dev-egress" for a
# COMPOSE_PROJECT_NAME=dev instance. The bash counterpart is proj_name() in
# bin/sh/lib/common.sh. Used so these scripts work for ANY instance, not just
# the default "ragclaw" project.
function Get-ProjectName {
    $p = $env:COMPOSE_PROJECT_NAME
    if (-not $p) {
        $envLine = Select-String -Path (Join-Path $Root ".env") -Pattern '^COMPOSE_PROJECT_NAME=' -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($envLine) { $p = ($envLine.Line -replace '^COMPOSE_PROJECT_NAME=').Trim() }
    }
    if (-not $p) { $p = Split-Path -Leaf $Root }
    return $p
}
