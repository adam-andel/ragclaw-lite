# RAGClaw REPL MCP Server Control Script (Python + Shell + JavaScript)
# Usage: .\bin\psl\mcp_repl.ps1 [start|stop|reload|status|build|logs]
#
# Container mode only: the REPL MCP server always runs as a Docker container
# (ragclaw-mcp-repl). Local Python venv execution is no longer supported — this
# project must run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down mcp-repl
#
# Languages: Python (always), Shell (enabled by default in container),
#           JavaScript (--enable-javascript, requires Node.js in image)

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path))
$ComposeFile = Join-Path $Root "docker-compose.yml"
$Port = 9200

# Shared Docker/compose/egress helpers (Test-Docker, Assert-Docker,
# Test-ComposeAvailable, Repair-EgressNetwork, Get-ProjectName) — also
# resolves the latent Get-ProjectName dependency used below.
. (Join-Path $PSScriptRoot "lib\common.ps1")
# Shared Docker registry-mirror probing (Get-WorkingMirrorDomain, etc.)
. (Join-Path $PSScriptRoot "lib\mirror.ps1")

# Images the mcp-repl / egress Dockerfiles pull (python only).
$RequiredImages = @("library/python:3.12-slim")

# =====================================================================
# Helpers
# =====================================================================

# Shared Docker / compose / egress helpers (Assert-Docker, Test-Docker,
# Test-ComposeAvailable, Repair-EgressNetwork) are sourced from
# lib/common.ps1. Only REPL-specific helpers remain below.

function Test-DockerRepl {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=$(Get-ProjectName)-mcp-repl" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Test-DockerEgress {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=$(Get-ProjectName)-egress" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

# =====================================================================
# Actions: Docker mode (container mode only)
# =====================================================================

function Start-DockerRepl {
    Assert-Docker
    Write-Host "=== REPL MCP Server (Docker :$Port) ===" -ForegroundColor Cyan

    if (Test-DockerRepl) {
        Write-Host "Already running on :$Port (Docker mode)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-ComposeAvailable $ComposeFile "mcp-repl")) {
        Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service" -ForegroundColor Red
        return
    }

    # Find working mirror for build-time image pull (FROM line uses this)
    $buildMirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
    if (-not $buildMirror) {
        Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
        return
    }
    Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror mcp-repl
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: build failed" -ForegroundColor Red
        return
    }

    Write-Host ""
    Write-Host "=== Starting container ===" -ForegroundColor Cyan
    # ragclaw-egress owns a fixed internal IP (172.30.0.2) on the "$(Get-ProjectName)_ragclaw-internal"
    # network. A stale, non-running egress container (or a stuck IPAM lease on
    # the network left behind after a prior `down`) can keep that IP occupied
    # and make `up` fail with "Address already in use". Clean both first.
    Repair-EgressNetwork

    docker compose -f $ComposeFile up -d mcp-repl ragclaw-egress
    if ($LASTEXITCODE -ne 0) {
        # Second chance: the fixed egress IP is likely still leased on the
        # "$(Get-ProjectName)_ragclaw-internal" network. Tear the network down (releasing the IPAM
        # lease) and retry the bring-up once.
        Write-Host "  First attempt failed; releasing "$(Get-ProjectName)_ragclaw-internal" network lease and retrying..." -ForegroundColor DarkYellow
        Repair-EgressNetwork -ForceNetwork
        docker compose -f $ComposeFile up -d mcp-repl ragclaw-egress
    }
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        Write-Host "       The fixed egress IP (172.30.0.2) is still leased on the" -ForegroundColor DarkYellow
        Write-Host "       "$(Get-ProjectName)_ragclaw-internal" network and could not be auto-recovered." -ForegroundColor DarkYellow
        Write-Host "       Run these manually, then start again:" -ForegroundColor DarkYellow
        Write-Host "         docker compose -f docker-compose.yml down" -ForegroundColor DarkYellow
        Write-Host "         docker network rm "$(Get-ProjectName)_ragclaw-internal"" -ForegroundColor DarkYellow
        Write-Host "         docker compose -f docker-compose.yml up -d" -ForegroundColor DarkYellow
        return
    }

    Start-Sleep 3
    if (Test-DockerRepl) {
        Write-Host "REPL server started (Docker)" -ForegroundColor Green
        Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
        Write-Host "  Workspace: persistent volume ragclaw_workspace (survives restart)" -ForegroundColor Gray
        Write-Host "  Mode: Docker container (ragclaw-mcp-repl)" -ForegroundColor Gray
        Write-Host "  Resources: memory=896M, cpus=2" -ForegroundColor Gray
        if (Test-DockerEgress) {
            Write-Host "  Egress broker: running (ragclaw-egress)" -ForegroundColor Gray
        }
        else {
            Write-Host "  Egress broker: NOT running (ragclaw-egress)" -ForegroundColor DarkYellow
        }
    }
    else {
        Write-Host "WARNING: Container not responding, check: docker logs ragclaw-mcp-repl" -ForegroundColor Yellow
    }
}

function Stop-DockerRepl {
    Write-Host "=== Stopping REPL server + egress broker (Docker) ===" -ForegroundColor Cyan
    if (Test-DockerRepl) {
        docker compose -f $ComposeFile stop mcp-repl
        if ($LASTEXITCODE -eq 0) {
            Write-Host "REPL server stopped (Docker)" -ForegroundColor Green
        }
    }
    else {
        Write-Host "REPL server not running (Docker)" -ForegroundColor Yellow
    }

    if (Test-DockerEgress) {
        docker compose -f $ComposeFile stop ragclaw-egress
        if ($LASTEXITCODE -eq 0) {
            Write-Host "Egress broker stopped (Docker)" -ForegroundColor Green
        }
    }
    else {
        Write-Host "Egress broker not running (Docker)" -ForegroundColor Yellow
    }
}

function Show-Status {
    Write-Host "=== Python REPL MCP Server Status ===" -ForegroundColor Cyan
    Write-Host "  Port: $Port" -ForegroundColor Gray
    Write-Host "  Mode: Docker container (container mode only)" -ForegroundColor Cyan

    if (Test-DockerRepl) {
        Write-Host "  Status: running (ragclaw-mcp-repl)" -ForegroundColor Green
        $startedAt = docker inspect "$(Get-ProjectName)-mcp-repl" --format '{{.State.StartedAt}}' 2>$null
        if ($startedAt) { Write-Host "  Since:  $startedAt" -ForegroundColor Gray }
    }
    else {
        Write-Host "  Status: REPL server NOT running" -ForegroundColor Red
    }

    if (Test-DockerEgress) {
        Write-Host "  Egress broker: running (ragclaw-egress)" -ForegroundColor Green
        $egressSince = docker inspect "$(Get-ProjectName)-egress" --format '{{.State.StartedAt}}' 2>$null
        if ($egressSince) { Write-Host "    Since:  $egressSince" -ForegroundColor Gray }
    }
    else {
        Write-Host "  Egress broker: NOT running" -ForegroundColor DarkYellow
    }

    if (-not (Test-DockerRepl) -and -not (Test-DockerEgress)) {
        if (Test-Docker) {
            Write-Host "  Docker: available (nothing running)" -ForegroundColor Yellow
        }
        else {
            Write-Host "  Docker: not installed" -ForegroundColor Yellow
        }
    }
}

# =====================================================================
# Dispatch
# =====================================================================

switch ($Action) {
    "start" {
        Start-DockerRepl
    }

    "stop" {
        Stop-DockerRepl
    }

    "status" { Show-Status }

    "build" {
        Assert-Docker
        $buildMirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
        if (-not $buildMirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        Write-Host "Rebuilding mcp-repl image (registry: $buildMirror, --no-cache) ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror --no-cache mcp-repl
    }

    "reload" {
        Assert-Docker
        if (-not (Test-ComposeAvailable $ComposeFile "mcp-repl")) {
            Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service" -ForegroundColor Red
            return
        }

        if (Test-DockerRepl) {
            Stop-DockerRepl
        }

        $buildMirror = Get-WorkingMirrorDomain -RequiredImages $RequiredImages
        if (-not $buildMirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
        docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror mcp-repl
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: build failed" -ForegroundColor Red
            return
        }

        Write-Host ""
        Write-Host "=== Starting container ===" -ForegroundColor Cyan
        Repair-EgressNetwork
        docker compose -f $ComposeFile up -d mcp-repl ragclaw-egress
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  First attempt failed; releasing "$(Get-ProjectName)_ragclaw-internal" network lease and retrying..." -ForegroundColor DarkYellow
            Repair-EgressNetwork -ForceNetwork
            docker compose -f $ComposeFile up -d mcp-repl ragclaw-egress
        }
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
            Write-Host "       The fixed egress IP (172.30.0.2) is still leased on the" -ForegroundColor DarkYellow
            Write-Host "       "$(Get-ProjectName)_ragclaw-internal" network. Run: docker compose down ; docker network rm "$(Get-ProjectName)_ragclaw-internal" ; docker compose up -d" -ForegroundColor DarkYellow
            return
        }

        Start-Sleep 3
        if (Test-DockerRepl) {
            Write-Host "REPL server reloaded (Docker)" -ForegroundColor Green
            Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
            Write-Host "  Workspace: persistent volume ragclaw_workspace (survives restart)" -ForegroundColor Gray
            Write-Host "  Mode: Docker container (ragclaw-mcp-repl)" -ForegroundColor Gray
            Write-Host "  Resources: memory=896M, cpus=2" -ForegroundColor Gray
        }
        else {
            Write-Host "WARNING: Container not responding, check: docker logs ragclaw-mcp-repl" -ForegroundColor Yellow
        }
    }

    "logs" {
        if (Test-DockerRepl) { docker logs --tail=50 -f ragclaw-mcp-repl }
        else { Write-Host "REPL server not running in Docker mode" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Usage: .\bin\psl\mcp_repl.ps1 [start|stop|reload|status|build|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  start       Start REPL server (build + up, container mode)"
        Write-Host "  stop        Stop REPL server"
        Write-Host "  reload     Stop, rebuild image (uses cache), and start REPL server"
        Write-Host "  status      Show running status"
        Write-Host "  build       Rebuild Docker image only (--no-cache)"
        Write-Host "  logs        Tail Docker container logs"
    }
}
