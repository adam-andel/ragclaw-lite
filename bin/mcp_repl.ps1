# ERAG REPL MCP Server Control Script (Python + Shell + JavaScript)
# Usage: .\bin\mcp_repl.ps1 [start|stop|restart|status|build|logs]
#
# Container mode only: the REPL MCP server always runs as a Docker container
# (erag-mcp-repl). Local Python venv execution is no longer supported — this
# project must run in container mode.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down mcp-repl
#
# Languages: Python (always), Shell (enabled by default in container),
#           JavaScript (--enable-javascript, requires Node.js in image)

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$ComposeFile = Join-Path $Root "docker-compose.yml"
$Port = 9200

# Mirror sources (China-friendly, tested in order)
$MirrorList = @(
    "https://docker.m.daocloud.io"
    "https://docker.1ms.run"
)

# =====================================================================
# Helpers
# =====================================================================

function Assert-Docker {
    if (-not (Test-Docker)) {
        Write-Host "ERROR: Docker is not installed or not running." -ForegroundColor Red
        Write-Host "       This project runs in container mode only. Please install Docker Desktop." -ForegroundColor Yellow
        exit 1
    }
}

function Test-Docker {
    try { $null = docker --version 2>$null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}

function Test-ComposeAvailable {
    param([string]$ComposePath)
    if (-not (Test-Path $ComposePath)) { return $false }
    $yml = Get-Content $ComposePath -Raw
    return $yml -match 'mcp-repl:'
}

function Test-DockerRepl {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=erag-mcp-repl" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

# =====================================================================
# Docker mirror helpers
# =====================================================================

function Get-WorkingMirrorDomain {
    <#
    .SYNOPSIS
    Returns the first working registry domain, trying daemon.json mirrors first,
    then docker.io, then the hardcoded $MirrorList as a last resort.
    Respects user's daemon.json edits (add/remove/comment-out via JSON).
    Returns $null if no mirror is reachable.
    #>
    $candidates = @(Get-ExistingMirrors)
    foreach ($m in $candidates) {
        $domain = $m -replace '^https?://', ''
        Write-Host "  Testing $domain ..." -ForegroundColor DarkGray
        if (-not (Test-Registry $m)) {
            Write-Host "    /v2/ unreachable" -ForegroundColor DarkYellow
            continue
        }
        $imageOk = Test-MirrorImage -Domain $domain -Image "library/python" -Tag "3.12-slim"
        if ($imageOk) { return $domain }
        Write-Host "    python:3.12-slim unavailable, trying next..." -ForegroundColor DarkYellow
    }
    Write-Host "  WARNING: No mirrors in daemon.json or no daemon.json mirror can serve python:3.12-slim, falling back to docker.io" -ForegroundColor DarkYellow
    if (Test-Registry "https://hub.docker.com") { return "docker.io" }
    Write-Host "  hub.docker.com NOT reachable, using backup mirrors" -ForegroundColor DarkYellow

    foreach ($m in $MirrorList) {
        $domain = $m -replace '^https?://', ''
        Write-Host "  Testing $domain ..." -ForegroundColor DarkGray
        if (-not (Test-Registry $m)) {
            Write-Host "    /v2/ unreachable" -ForegroundColor DarkYellow
            continue
        }
        $imageOk = Test-MirrorImage -Domain $domain -Image "library/python" -Tag "3.12-slim"
        if (-not $imageOk) {
            Write-Host "    python:3.12-slim unavailable, trying next..." -ForegroundColor DarkYellow
            continue
        }
        return $domain
    }
    Write-Host "FAIL: no mirror reachable, check network" -ForegroundColor Red
    return $null
}

function Test-MirrorImage {
    param([string]$Domain, [string]$Image, [string]$Tag)
    # Check if mirror can serve a specific image manifest without 429 rate-limit
    $url = "https://$Domain/v2/$Image/manifests/$Tag"
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.UserAgent = "ERAG-Mirror-Checker"
        $req.Timeout = 8000
        $req.AllowAutoRedirect = $false
        $req.Method = "HEAD"
        $req.Accept = "application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json"
        $resp = $req.GetResponse()
        $code = $resp.StatusCode.value__
        $resp.Close()
        # 200 = found, 401 = needs auth (but registry is alive)
        return ($code -eq 200 -or $code -eq 401)
    }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $code = $_.Exception.Response.StatusCode.value__
            $_.Exception.Response.Close()
            if ($code -eq 429) { return $false }  # rate-limited
            if ($code -eq 401) { return $true }   # needs auth, but reachable
            return ($code -eq 200)
        }
        return $false
    }
    catch { return $false }
}

function Test-Registry {
    param([string]$Url)
    # Docker Registry API v2: /v2/ returns 200 (no auth) or 401 (auth required).
    # Both mean the registry is alive. Other codes or connection errors = unreachable.
    $testUrl = $Url.TrimEnd('/') + "/v2/"
    try {
        $req = [System.Net.HttpWebRequest]::Create($testUrl)
        $req.UserAgent = "ERAG-Mirror-Checker"
        $req.Timeout = 5000
        $req.AllowAutoRedirect = $true
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $code = $resp.StatusCode.value__
        $resp.Close()
        return ($code -eq 200 -or $code -eq 401)
    }
    catch [System.Net.WebException] {
        # WebException still carries the response on protocol errors
        if ($_.Exception.Response) {
            $code = $_.Exception.Response.StatusCode.value__
            $_.Exception.Response.Close()
            return ($code -eq 200 -or $code -eq 401)
        }
        return $false
    }
    catch { return $false }
}

function Get-ExistingMirrors {
    $cfgPath = "$env:USERPROFILE\.docker\daemon.json"
    if (-not (Test-Path $cfgPath)) { return @() }
    try {
        $raw = Get-Content $cfgPath -Raw -ErrorAction Stop
        $cfg = $raw | ConvertFrom-Json -ErrorAction Stop
        if ($cfg.'registry-mirrors') { return @($cfg.'registry-mirrors') }
    }
    catch { }
    return @()
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

    if (-not (Test-ComposeAvailable $ComposeFile)) {
        Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service" -ForegroundColor Red
        return
    }

    # Find working mirror for build-time image pull (FROM line uses this)
    $buildMirror = Get-WorkingMirrorDomain
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
    docker compose -f $ComposeFile up -d mcp-repl
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        return
    }

    Start-Sleep 3
    if (Test-DockerRepl) {
        Write-Host "REPL server started (Docker)" -ForegroundColor Green
        Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
        Write-Host "  Workspace: tmpfs (512M, auto-cleaned on stop)" -ForegroundColor Gray
        Write-Host "  Mode: Docker container (erag-mcp-repl)" -ForegroundColor Gray
        Write-Host "  Resources: memory=768M, cpus=2" -ForegroundColor Gray
    }
    else {
        Write-Host "WARNING: Container not responding, check: docker logs erag-mcp-repl" -ForegroundColor Yellow
    }
}

function Stop-DockerRepl {
    Write-Host "=== Stopping REPL server (Docker) ===" -ForegroundColor Cyan
    if (Test-DockerRepl) {
        docker compose -f $ComposeFile stop mcp-repl
        if ($LASTEXITCODE -eq 0) {
            Write-Host "REPL server stopped (Docker)" -ForegroundColor Green
        }
    }
    else {
        Write-Host "REPL server not running (Docker)" -ForegroundColor Yellow
    }
}

function Show-Status {
    Write-Host "=== Python REPL MCP Server Status ===" -ForegroundColor Cyan
    Write-Host "  Port: $Port" -ForegroundColor Gray
    Write-Host "  Mode: Docker container (container mode only)" -ForegroundColor Cyan

    if (Test-DockerRepl) {
        Write-Host "  Status: running (erag-mcp-repl)" -ForegroundColor Green
        $startedAt = docker inspect erag-mcp-repl --format '{{.State.StartedAt}}' 2>$null
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
        Start-DockerRepl
    }

    "stop" {
        Stop-DockerRepl
    }

    "status" { Show-Status }

    "build" {
        Assert-Docker
        $buildMirror = Get-WorkingMirrorDomain
        if (-not $buildMirror) {
            Write-Host "ERROR: no working mirror available (all registries rate-limited or unreachable)" -ForegroundColor Red
            return
        }
        Write-Host "Rebuilding mcp-repl image (registry: $buildMirror, --no-cache) ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror --no-cache mcp-repl
    }

    "restart" {
        Assert-Docker
        if (-not (Test-ComposeAvailable $ComposeFile)) {
            Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service" -ForegroundColor Red
            return
        }

        if (Test-DockerRepl) {
            Stop-DockerRepl
        }

        $buildMirror = Get-WorkingMirrorDomain
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
        docker compose -f $ComposeFile up -d mcp-repl
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
            return
        }

        Start-Sleep 3
        if (Test-DockerRepl) {
            Write-Host "REPL server restarted (Docker)" -ForegroundColor Green
            Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
            Write-Host "  Workspace: tmpfs (512M, auto-cleaned on stop)" -ForegroundColor Gray
            Write-Host "  Mode: Docker container (erag-mcp-repl)" -ForegroundColor Gray
            Write-Host "  Resources: memory=768M, cpus=2" -ForegroundColor Gray
        }
        else {
            Write-Host "WARNING: Container not responding, check: docker logs erag-mcp-repl" -ForegroundColor Yellow
        }
    }

    "logs" {
        if (Test-DockerRepl) { docker logs --tail=50 -f erag-mcp-repl }
        else { Write-Host "REPL server not running in Docker mode" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Usage: .\bin\mcp_repl.ps1 [start|stop|restart|status|build|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  start       Start REPL server (build + up, container mode)"
        Write-Host "  stop        Stop REPL server"
        Write-Host "  restart     Stop, rebuild image (uses cache), and start REPL server"
        Write-Host "  status      Show running status"
        Write-Host "  build       Rebuild Docker image only (--no-cache)"
        Write-Host "  logs        Tail Docker container logs"
    }
}
