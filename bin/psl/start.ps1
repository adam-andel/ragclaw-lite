# ERAG All-in-one Control Script
# Usage: .\bin\psl\start.ps1 [start|stop|reload|status]
#
# Container mode only: the backend runs containerized and serves the frontend
# from the container — no local Vite / local Python is used. This project must
# run in container mode.
#
# This script drives `docker compose` directly for the base stack
# (erag / mcp-repl / erag-egress). It does NOT delegate to backend.ps1 or
# mcp_repl.ps1 — those remain available for per-service control.

param([string]$Action = "start")

$BinDir      = Split-Path -Parent $MyInvocation.MyCommand.Path
$Root        = Split-Path -Parent (Split-Path -Parent $BinDir)
$ComposeFile = Join-Path $Root "docker-compose.yml"

# Shared Docker registry-mirror probing (Get-WorkingMirrorDomain, etc.)
. (Join-Path $PSScriptRoot "lib\mirror.ps1")
# Shared helpers (Get-EragPublishedPort — real host port resolver)
. (Join-Path $PSScriptRoot "lib\common.ps1")

# start builds the whole stack; erag is multi-stage (node + python), the other
# services are python-only, so the union is python:3.12-slim + node:22-alpine.
$RequiredImages = @("library/python:3.12-slim", "library/node:22-alpine")

# =====================================================================
# Helpers
# =====================================================================

function Test-Docker {
    try { $null = docker --version 2>$null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}

function Assert-Docker {
    if (-not (Test-Docker)) {
        Write-Host "ERROR: Docker is not installed or not running." -ForegroundColor Red
        Write-Host "       This project runs in container mode only. Please install Docker Desktop." -ForegroundColor Yellow
        exit 1
    }
}

# Free the fixed egress IP (172.30.0.2) on the erag-internal network so `up`
# does not fail with "Address already in use". Removes a stale (non-running)
# erag-egress container, and with -ForceNetwork also tears down the network to
# release a stuck Docker IPAM lease left behind by a prior `down`.
function Repair-EgressNetwork {
    param([switch]$ForceNetwork)

    if (-not (Test-Docker)) { return }

    $egressId = docker ps -a -q -f "name=erag-egress" 2>$null
    if ($egressId) {
        $running = docker ps -q -f "name=erag-egress" 2>$null
        if (-not $running) {
            Write-Host "  Removing stale erag-egress container to free its fixed IP..." -ForegroundColor DarkGray
            docker compose -f $ComposeFile rm -f erag-egress 2>$null | Out-Null
        }
    }

    if (-not $ForceNetwork) { return }

    $attached = docker network inspect erag-internal --format '{{range $k,$v := .Containers}}{{$k}}{{"\n"}}{{end}}' 2>$null
    if ($attached) { $attached | ForEach-Object { if ($_) { docker rm -f $_ 2>$null | Out-Null } } }
    docker network rm erag-internal 2>$null | Out-Null
    Write-Host "  Released erag-internal network IPAM lease; will recreate on up." -ForegroundColor DarkGray
}

function Wait-ForBackend {
    $realPort = Get-EragPublishedPort
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
    Write-Host "  Check manually: docker logs erag-lite" -ForegroundColor Gray
    return $false
}

function Build-Stack([string]$Mirror) {
    Write-Host "=== Building (registry: $Mirror) ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile build --build-arg REGISTRY=$Mirror
    return ($LASTEXITCODE -eq 0)
}

function Up-Stack([switch]$ForceRecreate) {
    $recreate = if ($ForceRecreate) { "--force-recreate" } else { "" }
    Repair-EgressNetwork
    docker compose -f $ComposeFile up -d $recreate
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  First attempt failed; releasing erag-internal network lease and retrying..." -ForegroundColor DarkYellow
        Repair-EgressNetwork -ForceNetwork
        docker compose -f $ComposeFile up -d $recreate
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        Write-Host "       The fixed egress IP (172.30.0.2) is still leased on the erag-internal" -ForegroundColor DarkYellow
        Write-Host "       network and could not be auto-recovered. Run manually, then start again:" -ForegroundColor DarkYellow
        Write-Host "         docker compose -f docker-compose.yml down" -ForegroundColor DarkYellow
        Write-Host "         docker network rm erag-internal" -ForegroundColor DarkYellow
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
        $mirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
        if (-not $mirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        if (-not (Build-Stack $mirror)) { return }
        Write-Host ""
        Write-Host "=== Starting stack ===" -ForegroundColor Cyan
        if (-not (Up-Stack)) { return }
        Wait-ForBackend
        $appPort = Get-EragPublishedPort
        Write-Host ""
        Write-Host "=== All services started (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App:     http://localhost:$appPort" -ForegroundColor Gray
        Write-Host "  Swagger: http://127.0.0.1:$appPort/docs" -ForegroundColor Gray
        Write-Host "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process "http://localhost:$appPort"
    }

    "reload" {
        Write-Host "=== Reloading all services ===" -ForegroundColor Cyan
        Assert-Docker
        if (-not (Test-Path $ComposeFile)) {
            Write-Host "ERROR: docker-compose.yml not found at $ComposeFile" -ForegroundColor Red
            return
        }
        $mirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
        if (-not $mirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        if (-not (Build-Stack $mirror)) { return }
        Write-Host ""
        Write-Host "=== Recreating stack ===" -ForegroundColor Cyan
        if (-not (Up-Stack -ForceRecreate)) { return }
        Wait-ForBackend
        $appPort = Get-EragPublishedPort
        Write-Host ""
        Write-Host "=== Reload complete (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App: http://localhost:$appPort" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process "http://localhost:$appPort"
    }

    "stop" {
        Write-Host "=== Stopping all services ===" -ForegroundColor Cyan
        docker compose -f $ComposeFile stop
        if ($LASTEXITCODE -eq 0) {
            Write-Host "All services stopped (Docker)" -ForegroundColor Green
        }
    }

    "status" {
        Write-Host "=== ERAG Service Status ===" -ForegroundColor Cyan
        Write-Host "  Mode: Docker container" -ForegroundColor Cyan
        docker compose -f $ComposeFile ps
        $running = docker ps -q -f "name=erag-lite" 2>$null
        if ($running) {
            $appPort = Get-EragPublishedPort
            $portSrc = if ($env:ERAG_PORT) { $env:ERAG_PORT } else { "<random>" }
            Write-Host "  App URL: http://localhost:$appPort  (ERAG_PORT: $portSrc)" -ForegroundColor Gray
        }
    }

    default {
        Write-Host "Usage: .\bin\psl\start.ps1 [start|stop|reload|status]" -ForegroundColor Yellow
    }
}
