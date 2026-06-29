# ERAG Python REPL MCP Server Control Script
# Usage: .\bin\mcp_repl.ps1 [start|stop|status|build|fix-mirror|logs]
#
# Smart mode: auto-detects Docker. If Docker is installed, runs containerized.
# If Docker is not installed, falls back to local Python venv.
#
# Mirror detection: before Docker build, automatically checks if image registries
# are reachable from China. If not, configures domestic mirrors in daemon.json.
# Adapted from Fix-DockerMirrors.ps1.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down
# Local  mode uses:  .\mcp\venv + python_repl_mcp_server.py

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$McpDir = Join-Path $Root "mcp"
$VenvDir = Join-Path $McpDir "venv"
$ServerScript = Join-Path $McpDir "python_repl_mcp_server.py"
$WorkDir = "$Root\data\workspace"
$Port = 9200
$ComposeFile = Join-Path $Root "docker-compose.yml"

# Mirror sources (China-friendly, tested in order)
$MirrorList = @(
    "https://docker.m.daocloud.io"
    "https://docker.1ms.run"
)

# Ensure workspace exists (used by both modes)
if (-not (Test-Path $WorkDir)) {
    New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null
}

# =====================================================================
# Helpers
# =====================================================================

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

function Test-LocalRepl {
    try {
        $body = '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" `
            -Method Post -ContentType "application/json" -Body $body `
            -TimeoutSec 3 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

# =====================================================================
# Docker mirror helpers
# =====================================================================

function Get-WorkingMirrorDomain {
    <#
    .SYNOPSIS
    Returns the first working registry domain for use as --build-arg REGISTRY.
    Tests both /v2/ reachability AND image manifest availability (detects 429 rate-limit).
    Falls back to "docker.io" if hub.docker.com is reachable.
    #>
    if (Test-Registry "https://hub.docker.com") { return "docker.io" }
    $candidates = @(Get-ExistingMirrors) + $MirrorList | Select-Object -Unique
    foreach ($m in $candidates) {
        $domain = $m -replace '^https?://', ''
        # First check registry endpoint
        if (-not (Test-Registry $m)) { continue }
        # Then check the actual image we need (catches 429 rate-limit)
        $imageOk = Test-MirrorImage -Domain $domain -Image "library/python" -Tag "3.12-slim"
        if ($imageOk) { return $domain }
        Write-Host "  $m OK for /v2/ but image blocked (429), trying next..." -ForegroundColor DarkYellow
    }
    Write-Host "  WARNING: no mirror can serve python:3.12-slim, falling back to docker.io" -ForegroundColor Yellow
    return "docker.io"
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

function Test-DockerCanPull {
    if (Test-Registry "https://hub.docker.com") { return $true }
    $mirrors = Get-ExistingMirrors
    foreach ($m in $mirrors) {
        if (Test-Registry $m) { return $true }
    }
    return $false
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

function Read-DaemonConfig {
    $cfgPath = "$env:USERPROFILE\.docker\daemon.json"
    $result = @{ Path = $cfgPath; Raw = ""; Keys = @{} }
    if (Test-Path $cfgPath) {
        try {
            $result.Raw = Get-Content $cfgPath -Raw -ErrorAction Stop
            $obj = $result.Raw | ConvertFrom-Json -ErrorAction Stop
            $ht = @{}
            foreach ($prop in $obj.PSObject.Properties) {
                $ht[$prop.Name] = $prop.Value
            }
            $result.Keys = $ht
        }
        catch { }
    }
    return $result
}

function Write-DaemonConfig {
    param([hashtable]$Keys)
    $cfgPath = "$env:USERPROFILE\.docker\daemon.json"
    $tmpPath = [System.IO.Path]::GetTempFileName()
    try {
        $Keys | ConvertTo-Json -Depth 10 | Set-Content $tmpPath -Encoding UTF8
        if (Test-Path $cfgPath) {
            Copy-Item $cfgPath "$cfgPath.erag-bak" -Force
            Write-Host "  已备份: $cfgPath.erag-bak" -ForegroundColor DarkGray
        }
        Move-Item $tmpPath $cfgPath -Force
    }
    catch {
        Write-Host "  写入 daemon.json 失败: $_" -ForegroundColor Red
    }
}

function Ensure-DockerMirror {
    Write-Host "[mirror] Checking Docker registry access ..." -ForegroundColor DarkGray

    # 1. Official hub reachable, nothing to do
    if (Test-Registry "https://hub.docker.com") {
        Write-Host "  OK: hub.docker.com reachable" -ForegroundColor DarkGray
        return $true
    }
    Write-Host "  hub.docker.com NOT reachable" -ForegroundColor DarkYellow

    # 2. Check existing mirrors
    $existing = Get-ExistingMirrors
    $working = @()
    foreach ($m in $existing) {
        $ok = Test-Registry $m
        $mark = if ($ok) { "OK" } else { "FAIL" }
        $color = if ($ok) { "Green" } else { "Red" }
        Write-Host "  $m ... $mark" -ForegroundColor $color
        if ($ok) { $working += $m }
    }

    if ($working.Count -gt 0) {
        Write-Host "  OK: $($working.Count) existing mirror(s) working" -ForegroundColor DarkGray
        return $true
    }

    # 3. No working mirrors, configure preferred ones
    Write-Host "  No working mirrors, configuring ..." -ForegroundColor Yellow
    $newList = @()
    foreach ($m in $existing) { $newList += $m }
    $added = $false
    foreach ($m in $MirrorList) {
        if ($m -in $newList) { continue }
        $ok = Test-Registry $m
        $mark = if ($ok) { "OK" } else { "FAIL" }
        $color = if ($ok) { "Green" } else { "Red" }
        Write-Host "  $m ... $mark" -ForegroundColor $color
        if ($ok) {
            $newList += $m
            $added = $true
        }
    }

    if (-not $added) {
        Write-Host "  FAIL: no mirror reachable, check network" -ForegroundColor Red
        return $false
    }

    # Write config preserving existing keys
    $cfg = Read-DaemonConfig
    $cfg.Keys['registry-mirrors'] = $newList
    Write-DaemonConfig -Keys $cfg.Keys

    Write-Host "  OK: $($newList.Count) mirrors written" -ForegroundColor Green
    Write-Host ""
    Write-Host "  NOTE: restart Docker Desktop for mirror config to take effect" -ForegroundColor Yellow
    Write-Host "  Right-click Docker tray icon -> Quit Docker Desktop -> reopen" -ForegroundColor Yellow
    return $false
}

# =====================================================================
# Actions
# =====================================================================

function Start-LocalRepl {
    Write-Host "=== Python REPL MCP Server (local :$Port) ===" -ForegroundColor Cyan

    if (Test-LocalRepl) {
        Write-Host "Already running on :$Port (local mode)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-Path $VenvDir)) {
        Write-Host "Creating venv at $VenvDir ..." -ForegroundColor Gray
        py -3.12 -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to create venv (is Python 3.12 installed?)" -ForegroundColor Red
            return
        }
        Write-Host "Installing packages ..." -ForegroundColor Gray
        & "$VenvDir\Scripts\python.exe" -m pip install --quiet pandas python-docx python-pptx PyPDF2
        Write-Host "Venv ready" -ForegroundColor Green
    }

    if (-not (Test-Path $ServerScript)) {
        Write-Host "ERROR: $ServerScript not found" -ForegroundColor Red
        return
    }

    Write-Host "Starting REPL server (workspace: $WorkDir) ..." -ForegroundColor Gray
    $proc = Start-Process -FilePath "$VenvDir\Scripts\python.exe" `
        -ArgumentList $ServerScript, "--port", $Port, "--allow-dir", $WorkDir, "--no-network", "--keep-minutes", "120", "--max-memory-mb", "512", "--max-nproc", "64", "--max-concurrent", "4" `
        -WorkingDirectory $McpDir -PassThru -WindowStyle Minimized

    Start-Sleep 3
    if (Test-LocalRepl) {
        Write-Host "REPL server started (PID: $($proc.Id))" -ForegroundColor Green
        Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
        Write-Host "  Workspace: $WorkDir" -ForegroundColor Gray
        Write-Host "  Mode: local (no Docker)" -ForegroundColor Gray
    }
    else {
        Write-Host "WARNING: Server may not have started, check console window" -ForegroundColor Yellow
    }
}

function Start-DockerRepl {
    Write-Host "=== Python REPL MCP Server (Docker :$Port) ===" -ForegroundColor Cyan

    if (Test-DockerRepl) {
        Write-Host "Already running on :$Port (Docker mode)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-ComposeAvailable $ComposeFile)) {
        Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service" -ForegroundColor Red
        Write-Host "       Fall back: .\bin\mcp_repl.ps1 start (auto-detects Docker absence)" -ForegroundColor Yellow
        return
    }

    # Mirror check before build
    if (-not (Ensure-DockerMirror)) {
        Write-Host "Docker mirrors not ready, aborting start." -ForegroundColor Red
        Write-Host "Fix manually: .\bin\mcp_repl.ps1 fix-mirror" -ForegroundColor Yellow
        return
    }

    # Find working mirror for build-time image pull (FROM line uses this)
    $buildMirror = Get-WorkingMirrorDomain
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
    if (Test-LocalRepl) {
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

function Stop-LocalRepl {
    Write-Host "=== Stopping REPL server (local) ===" -ForegroundColor Cyan
    try {
        Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -like "*python_repl_mcp_server*"
        } | Stop-Process -Force -ErrorAction SilentlyContinue
    }
    catch { }
    Start-Sleep 1
    if (-not (Test-LocalRepl)) {
        Write-Host "REPL server stopped" -ForegroundColor Green
    }
    else {
        Write-Host "WARNING: REPL server still responding" -ForegroundColor Yellow
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

    if (Test-DockerRepl) {
        Write-Host "  Mode:   Docker container" -ForegroundColor Green
        Write-Host "  Status: running (erag-mcp-repl)" -ForegroundColor Green
        $startedAt = docker inspect erag-mcp-repl --format '{{.State.StartedAt}}' 2>$null
        if ($startedAt) { Write-Host "  Since:  $startedAt" -ForegroundColor Gray }
        return
    }

    if (Test-LocalRepl) {
        Write-Host "  Mode:   Local Python" -ForegroundColor Yellow
        Write-Host "  Status: running on :$Port" -ForegroundColor Green
        Write-Host "  Workspace: $WorkDir" -ForegroundColor Gray
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

function Fix-DockerMirror {
    Write-Host "=== Docker Mirror Diagnostics ===" -ForegroundColor Cyan

    if (-not (Test-Docker)) {
        Write-Host "Docker not available" -ForegroundColor Red
        return
    }

    $cfg = Read-DaemonConfig
    Write-Host "Config file: $($cfg.Path)" -ForegroundColor Gray
    $existing = Get-ExistingMirrors
    Write-Host "Current mirrors ($($existing.Count)):" -ForegroundColor Gray
    foreach ($m in $existing) { Write-Host "  $m" -ForegroundColor DarkGray }

    if (Test-DockerCanPull) {
        Write-Host "OK: Docker registry reachable" -ForegroundColor Green
        return
    }

    Write-Host "Mirrors not reachable, auto-fixing ..." -ForegroundColor Yellow

    $newList = @()
    foreach ($m in $existing) { $newList += $m }
    $added = $false
    foreach ($m in $MirrorList) {
        if ($m -in $newList) { continue }
        $ok = Test-Registry $m
        $mark = if ($ok) { "OK" } else { "FAIL" }
        $color = if ($ok) { "Green" } else { "Red" }
        Write-Host "  $m ... $mark" -ForegroundColor $color
        if ($ok) {
            $newList += $m
            $added = $true
        }
    }

    if (-not $added -and $newList.Count -eq 0) {
        Write-Host "FAIL: no mirror reachable, check network" -ForegroundColor Red
        return
    }

    $cfg.Keys['registry-mirrors'] = $newList
    Write-DaemonConfig -Keys $cfg.Keys
    Write-Host "OK: $($newList.Count) mirrors written. Restart Docker Desktop to apply." -ForegroundColor Green
}

# =====================================================================
# Dispatch
# =====================================================================

switch ($Action) {
    "start" {
        if (Test-Docker) {
            Write-Host "[detect] Docker found, using container mode" -ForegroundColor DarkGray
            Start-DockerRepl
        }
        else {
            Write-Host "[detect] Docker not found, using local mode" -ForegroundColor DarkGray
            Start-LocalRepl
        }
    }

    "stop" {
        if (Test-DockerRepl) { Stop-DockerRepl }
        else { Stop-LocalRepl }
    }

    "status" { Show-Status }

    "build" {
        if (-not (Test-Docker)) {
            Write-Host "ERROR: Docker not available" -ForegroundColor Red
            return
        }
        if (-not (Ensure-DockerMirror)) {
            Write-Host "Mirrors not ready, run fix-mirror first" -ForegroundColor Red
            return
        }
        $buildMirror = Get-WorkingMirrorDomain
        Write-Host "Rebuilding mcp-repl image (registry: $buildMirror, --no-cache) ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror --no-cache mcp-repl
    }

    "fix-mirror" { Fix-DockerMirror }

    "restart" {
        # Fast restart without rebuild (container only)
        if (Test-DockerRepl) {
            Write-Host "Restarting mcp-repl container (no rebuild) ..." -ForegroundColor Gray
            docker compose -f $ComposeFile restart mcp-repl
            Start-Sleep 2
            if (Test-LocalRepl) {
                Write-Host "REPL server restarted" -ForegroundColor Green
            } else {
                Write-Host "WARNING: Container not responding after restart" -ForegroundColor Yellow
            }
        } elseif (Test-Docker) {
            Write-Host "Container not running. Use 'start' instead." -ForegroundColor Yellow
        } else {
            Write-Host "REPL server not running in Docker mode" -ForegroundColor Yellow
        }
    }

    "reload" {
        # Full redeploy: build (with visible output) then recreate container
        if (Test-Docker) {
            if (-not (Ensure-DockerMirror)) {
                Write-Host "Mirrors not ready, run fix-mirror first" -ForegroundColor Red
                return
            }
            $buildMirror = Get-WorkingMirrorDomain
            Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
            docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror mcp-repl
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: build failed" -ForegroundColor Red
                return
            }
            Write-Host ""
            Write-Host "=== Recreating container ===" -ForegroundColor Cyan
            docker compose -f $ComposeFile up -d mcp-repl
            Start-Sleep 2
            if (Test-LocalRepl) {
                Write-Host "REPL server reloaded" -ForegroundColor Green
            } else {
                Write-Host "WARNING: Check logs: docker logs erag-mcp-repl" -ForegroundColor Yellow
            }
        } else {
            Write-Host "Docker not available, use 'start' for local mode" -ForegroundColor Yellow
        }
    }

    "logs" {
        if (Test-DockerRepl) { docker logs --tail=50 -f erag-mcp-repl }
        else { Write-Host "REPL server not running in Docker mode" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Usage: .\bin\mcp_repl.ps1 [start|stop|restart|reload|status|build|fix-mirror|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  start       Start REPL server (build + up, auto: Docker or local fallback)"
        Write-Host "  stop        Stop REPL server"
        Write-Host "  restart     Restart container only (no rebuild, fast)"
        Write-Host "  reload      Rebuild image + restart (for code changes)"
        Write-Host "  status      Show running status"
        Write-Host "  build       Rebuild Docker image only (--no-cache)"
        Write-Host "  fix-mirror  Diagnose and fix Docker registry mirrors"
        Write-Host "  logs        Tail Docker container logs"
    }
}
