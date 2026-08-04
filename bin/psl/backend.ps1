# RAGClaw Backend Control Script
# Usage: .\bin\psl\backend.ps1 [start|stop|reload|status|build|logs]
#
# Container mode only: the backend always runs as a Docker container (ragclaw-lite).
# Local Python / uvicorn execution is no longer supported — this project must
# run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down ragclaw

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$ComposeFile = Join-Path $Root "docker-compose.yml"

# Shared Docker registry-mirror probing (Get-WorkingMirrorDomain, etc.)
. (Join-Path $PSScriptRoot "lib\mirror.ps1")
# Shared helpers (Get-RagclawPublishedPort — real host port resolver)
. (Join-Path $PSScriptRoot "lib\common.ps1")

# Images the ragclaw Dockerfile pulls (multi-stage: node build + python runtime).
$RequiredImages = @("library/python:3.12-slim", "library/node:22-alpine")

# =====================================================================
# Helpers
# Shared Docker / compose helpers (Assert-Docker, Test-Docker,
# Test-ComposeAvailable) are sourced from lib/common.ps1. Only
# backend-specific helpers remain below.
# =====================================================================

function Test-DockerBackend {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=$(Get-ProjectName)-lite" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Test-Backend {
    $realPort = Get-RagclawPublishedPort
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$realPort/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

# =====================================================================
# Actions: Docker mode (container mode only)
# =====================================================================

function Start-DockerBackend {
    Assert-Docker
    Write-Host "=== RAGClaw Backend (Docker) ===" -ForegroundColor Cyan

    if (Test-DockerBackend) {
        $realPort = Get-RagclawPublishedPort
        Write-Host "Backend already running on :$realPort (Docker mode)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-ComposeAvailable $ComposeFile "ragclaw")) {
        Write-Host "ERROR: docker-compose.yml missing or lacks 'ragclaw' service" -ForegroundColor Red
        return
    }

    # Find working mirror for build-time image pull
    $buildMirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
    if (-not $buildMirror) {
        Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
        return
    }
    $arg = @()
    if ($buildMirror -ne "docker.io") { $arg += "--build-arg", "REGISTRY=$buildMirror" }
    Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile build @arg ragclaw
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: build failed" -ForegroundColor Red
        return
    }

    Write-Host ""
    Write-Host "=== Starting container ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile up -d ragclaw
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        return
    }

    # Wait for health endpoint (model loading may take time)
    Write-Host "Waiting for startup (loading model, may take ~30s)..." -NoNewline
    for ($i = 0; $i -lt 90; $i++) {
        Start-Sleep 1
        if (Test-Backend) {
            Write-Host " OK" -ForegroundColor Green
            $realPort = Get-RagclawPublishedPort
            Write-Host "  Swagger: http://127.0.0.1:$realPort/docs" -ForegroundColor Gray
            Write-Host "  Mode: Docker container (ragclaw-lite)" -ForegroundColor Gray
            return
        }
        if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
    }
    Write-Host " timeout!" -ForegroundColor Red
    Write-Host "  Check manually: docker logs ragclaw-lite" -ForegroundColor Gray
}

function Stop-DockerBackend {
    Write-Host "=== Stopping backend (Docker) ===" -ForegroundColor Cyan
    if (Test-DockerBackend) {
        docker compose -f $ComposeFile stop ragclaw
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Backend stopped (Docker)" -ForegroundColor Green
        }
    }
    else {
        Write-Host "Backend not running (Docker)" -ForegroundColor Yellow
    }
}

# =====================================================================
# Status
# =====================================================================

function Show-Status {
    Write-Host "=== RAGClaw Backend Status ===" -ForegroundColor Cyan
    Write-Host "  Mode: Docker container (container mode only)" -ForegroundColor Cyan

    if (Test-DockerBackend) {
        $realPort = Get-RagclawPublishedPort
        Write-Host "  Port: $realPort" -ForegroundColor Gray
        Write-Host "  Status: running (ragclaw-lite)" -ForegroundColor Green
        $startedAt = docker inspect "$(Get-ProjectName)-lite" --format '{{.State.StartedAt}}' 2>$null
        if ($startedAt) { Write-Host "  Since:  $startedAt" -ForegroundColor Gray }
        return
    }

    if (Test-Docker) {
        Write-Host "  Docker: available (not running)" -ForegroundColor Yellow
    }
    else {
        Write-Host "  Docker: not installed" -ForegroundColor Yellow
    }
    Write-Host "  Status: NOT running" -ForegroundColor Red
}

# =====================================================================
# Dispatch
# =====================================================================

switch ($Action) {
    "start" {
        Start-DockerBackend
    }

    "stop" {
        Stop-DockerBackend
    }

    "status" { Show-Status }

    "build" {
        Assert-Docker
        $buildMirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
        if (-not $buildMirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        $arg = @()
        if ($buildMirror -ne "docker.io") { $arg += "--build-arg", "REGISTRY=$buildMirror" }
        Write-Host "Rebuilding ragclaw image (registry: $buildMirror, --no-cache) ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build @arg --no-cache ragclaw
    }

    "reload" {
        Assert-Docker
        if (-not (Test-ComposeAvailable $ComposeFile "ragclaw")) {
            Write-Host "ERROR: docker-compose.yml missing or lacks 'ragclaw' service" -ForegroundColor Red
            return
        }

        Write-Host "=== Recreating backend container (no image rebuild) ===" -ForegroundColor Cyan
        docker compose -f $ComposeFile up -d --force-recreate ragclaw
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
            return
        }

        Write-Host "Waiting for startup (loading model, may take ~30s) ..." -NoNewline
        for ($i = 0; $i -lt 90; $i++) {
            Start-Sleep 1
            if (Test-Backend) {
                Write-Host " OK" -ForegroundColor Green
                $realPort = Get-RagclawPublishedPort
                Write-Host "  Swagger: http://127.0.0.1:$realPort/docs" -ForegroundColor Gray
                Write-Host "  Mode: Docker container (ragclaw-lite)" -ForegroundColor Gray
                return
            }
            if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
        }
        Write-Host " timeout!" -ForegroundColor Red
        Write-Host "  Check manually: docker logs ragclaw-lite" -ForegroundColor Gray
    }

    "logs" {
        if (Test-DockerBackend) { docker logs --tail=50 -f "$(Get-ProjectName)-lite" }
        else { Write-Host "Backend not running in Docker mode" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Usage: .\bin\psl\backend.ps1 [start|stop|reload|status|build|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  start       Start backend (build + up, container mode)"
        Write-Host "  stop        Stop backend"
        Write-Host "  reload      Stop, recreate container (no image rebuild), and start backend"
        Write-Host "  status      Show running status (Docker)"
        Write-Host "  build       Rebuild Docker image only (--no-cache)"
        Write-Host "  logs        Tail Docker container logs"
    }
}
