# RAGClaw All-in-one Control Script
# Usage: .\bin\psl\start.ps1 [start|stop|reload|status]
#
# Container mode only: the backend runs containerized and serves the frontend
# from the container — no local Vite / local Python is used. This project must
# run in container mode.
#
# This script drives `docker compose` directly for the base stack
# (ragclaw / mcp-repl / ragclaw-egress). It does NOT delegate to backend.ps1 or
# mcp_repl.ps1 — those remain available for per-service control.
#
# Build sources (registry / apt / pypi) are passed EXPLICITLY via parameters and
# forwarded verbatim as --build-arg. No reachability probing is performed — this
# mirrors bin/sh/start.sh. Set them only when you want a mirror.
#
# NOTE: bin/sh provides an additional watch_mcp.sh (and integrates a hot-reload
# watcher into start.sh). That watcher is DEV-mode only - it restarts the mcp-repl
# container on source changes. Windows / psl never runs dev mode (container mode
# only, as stated above), so there is deliberately NO bin/psl/watch_mcp.ps1. The
# two directories are mirror sources for everything EXCEPT this dev-only script.

param(
    [string]$Action = "start",
    [string]$Registry = "",   # Docker base-image registry (empty -> docker.io)
    [string]$Apt = "",        # Debian apt mirror host (empty -> distro official)
    [string]$Pypi = ""        # PyPI index URL (empty -> official pypi.org)
)

$BinDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root        = Split-Path -Parent (Split-Path -Parent $BinDir)
$ComposeFile = Join-Path $Root "docker-compose.yml"

# Shared helpers (Get-RagclawPublishedPort — real host port resolver)
. (Join-Path $PSScriptRoot "lib\common.ps1")
# Secret generation (Initialize-RagclawSecrets — idempotent first-run generator)
. (Join-Path $PSScriptRoot "lib\gen-secrets.ps1")

# =====================================================================
# Helpers
# Shared Docker / egress helpers (Test-Docker, Assert-Docker,
# Repair-EgressNetwork) are sourced from lib/common.ps1. Only
# start.ps1-specific helpers remain below.
# =====================================================================

function Wait-ForBackend {
    $realPort = Get-RagclawPublishedPort
    Write-Host "Waiting for backend on :$realPort (loading model, may take ~30s)..." -NoNewline
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep 1
        try {
            $r = Invoke-WebRequest -Uri "http://127.0.0.1:$realPort/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
            if ($r.StatusCode -eq 200) { Write-Host " OK" -ForegroundColor Green; return $true }
        }
        catch { }
        if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
    }
    Write-Host " timeout!" -ForegroundColor Red
    Write-Host "  Check manually: docker logs ragclaw-lite" -ForegroundColor Gray
    return $false
}

# ---- nginx host-port resolvers (prod entry) ----
# Mirror bin/sh/lib/common.sh::nginx_published_port / nginx_https_enabled and
# bin/sh/start.sh::resolve_entry (prod branch). nginx is the sole entry in prod.
function Get-NginxPublishedPort([int]$ContainerPort = 80) {
    $out = docker compose -f $ComposeFile port nginx $ContainerPort 2>$null
    if ($out) {
        $port = ($out -split ':' | Select-Object -Last 1).Trim()
        if ($port -match '^\d+$') { return $port }
    }
    return $null
}

function Test-NginxHttpsEnabled {
    docker compose -f $ComposeFile exec -T nginx grep -q 'listen 443 ssl' /etc/nginx/conf.d/default.conf 2>$null
    return ($LASTEXITCODE -eq 0)
}

# Resolve the user-facing entry URL for the running (prod) stack: HTTP :80 always,
# HTTPS :443 when nginx serves TLS. Sets $AppUrl, $AppHttpUrl, $AppHttpsUrl.
function Resolve-Entry {
    $h = Get-NginxPublishedPort 80
    $s = Get-NginxPublishedPort 443
    if (Test-NginxHttpsEnabled -and $s) {
        $script:AppHttpsUrl = "https://localhost:$s"
        $script:AppUrl      = $script:AppHttpsUrl
        if ($h) { $script:AppHttpUrl = "http://localhost:$h" }
    } else {
        $script:AppHttpUrl = "http://localhost:$h"
        $script:AppUrl     = $script:AppHttpUrl
        $script:AppHttpsUrl = $null
    }
}

# Build-arg array from the explicit source params. Only inject a build-arg when
# the caller set it, so the common (official-source) path keeps all FROM + apt/pip
# layers cached — mirrors bin/sh/start.sh::build_stack().
function Get-BuildArgs {
    $argsArray = @()
    if ($Registry) { $argsArray += "--build-arg", "REGISTRY=$Registry" }
    if ($Apt)      { $argsArray += "--build-arg", "APT_MIRROR=$Apt" }
    if ($Pypi)     { $argsArray += "--build-arg", "PYPI_MIRROR=$Pypi" }
    return $argsArray
}

function Build-Stack([string[]]$BuildArgs) {
    Write-Host ("=== Building (registry: " + $(if ($Registry) { $Registry } else { "<official>" }) + ", apt: " + $(if ($Apt) { $Apt } else { "<official>" }) + ", pypi: " + $(if ($Pypi) { $Pypi } else { "<official>" }) + ") ===") -ForegroundColor Cyan
    docker compose --progress=plain -f $ComposeFile build @BuildArgs ragclaw mcp-repl ragclaw-egress nginx
    return ($LASTEXITCODE -eq 0)
}

function Up-Stack([switch]$ForceRecreate) {
    $recreate = if ($ForceRecreate) { "--force-recreate" } else { "" }
    Repair-EgressNetwork
    docker compose -f $ComposeFile up -d $recreate
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  First attempt failed; releasing "$(Get-ProjectName)_ragclaw-internal" network lease and retrying..." -ForegroundColor DarkYellow
        Repair-EgressNetwork -ForceNetwork
        docker compose -f $ComposeFile up -d $recreate
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        Write-Host "       The fixed egress IP (172.30.0.2) is still leased on the "$(Get-ProjectName)_ragclaw-internal"" -ForegroundColor DarkYellow
        Write-Host "       network and could not be auto-recovered. Run manually, then start again:" -ForegroundColor DarkYellow
        Write-Host "         docker compose -f docker-compose.yml down" -ForegroundColor DarkYellow
        Write-Host "         docker network rm "$(Get-ProjectName)_ragclaw-internal"" -ForegroundColor DarkYellow
        Write-Host "         docker compose -f docker-compose.yml up -d" -ForegroundColor DarkYellow
        return $false
    }
    return $true
}

# =====================================================================
# Actions
# =====================================================================

switch ($Action) {
    "start" {
        Assert-Docker
        if (-not (Test-Path $ComposeFile)) {
            Write-Host "ERROR: docker-compose.yml not found at $ComposeFile" -ForegroundColor Red
            return
        }
        $buildArgs = Get-BuildArgs
        Initialize-RagclawSecrets
        if (-not (Build-Stack $buildArgs)) { return }
        Write-Host ""
        Write-Host "=== Starting stack ===" -ForegroundColor Cyan
        if (-not (Up-Stack)) { return }
        Resolve-Entry
        Wait-ForBackend
        Write-Host ""
        Write-Host "=== All services started (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App:     $AppUrl" -ForegroundColor Gray
        if ($AppHttpsUrl -and $AppHttpsUrl -ne $AppUrl) { Write-Host "  HTTPS:   $AppHttpsUrl" -ForegroundColor Gray }
        Write-Host "  Swagger: $AppHttpUrl/docs" -ForegroundColor Gray
        Write-Host "  REPL:    internal only (mcp-repl:9200)" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process $AppUrl
    }

    "reload" {
        # Container-only reload: recreate containers from the EXISTING images and
        # never rebuild. Assumes the stack has been started at least once (so the
        # images already exist locally). No mirror probe, no secret regeneration,
        # no build — purely `up -d --force-recreate`. Mirrors bin/sh/start.sh reload.
        Write-Host "=== Recreating stack (containers only, no image rebuild) ===" -ForegroundColor Cyan
        Assert-Docker
        if (-not (Test-Path $ComposeFile)) {
            Write-Host "ERROR: docker-compose.yml not found at $ComposeFile" -ForegroundColor Red
            return
        }
        if (-not (Up-Stack -ForceRecreate)) { return }
        Resolve-Entry
        Wait-ForBackend
        Write-Host ""
        Write-Host "=== Reload complete (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App:     $AppUrl" -ForegroundColor Gray
        if ($AppHttpsUrl -and $AppHttpsUrl -ne $AppUrl) { Write-Host "  HTTPS:   $AppHttpsUrl" -ForegroundColor Gray }
        Write-Host "  Swagger: $AppHttpUrl/docs" -ForegroundColor Gray
        Write-Host "  REPL:    internal only (mcp-repl:9200)" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process $AppUrl
    }

    "stop" {
        Write-Host "=== Stopping all services ===" -ForegroundColor Cyan
        docker compose -f $ComposeFile stop
        if ($LASTEXITCODE -eq 0) {
            Write-Host "All services stopped (Docker)" -ForegroundColor Green
        }
    }

    "status" {
        Write-Host "=== RAGClaw Service Status ===" -ForegroundColor Cyan
        Write-Host "  Mode: Docker container" -ForegroundColor Cyan
        docker compose -f $ComposeFile ps
        $running = docker ps -q -f "name=$(Get-ProjectName)-lite" 2>$null
        if ($running) {
            Resolve-Entry
            $portSrc = if ($env:RAGCLAW_PORT) { $env:RAGCLAW_PORT } else { "<random>" }
            Write-Host "  App URL: $AppUrl  (entry: nginx, RAGCLAW_PORT: $portSrc)" -ForegroundColor Gray
        }
    }

    default {
        Write-Host "Usage: .\bin\psl\start.ps1 [start|stop|reload|status]" -ForegroundColor Yellow
    }
}
