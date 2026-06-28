# ERAG Backend Control Script
# Usage: .\bin\backend.ps1 [start|stop|status|build|fix-mirror|logs]
#
# Smart mode: auto-detects Docker. If Docker is installed, runs containerized
# (erag-lite). If Docker is not installed, falls back to local Python.
# Adapted from mcp_repl.ps1 pattern.
#
# Docker mode uses: docker compose -f docker-compose.yml up/down erag
# Local  mode uses:  py -3.12 -m uvicorn app.main:app ... (the original way)

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendDir = Join-Path $Root "backend"
$EnvFile = Join-Path $Root ".env"
$Port = 8000
$ComposeFile = Join-Path $Root "docker-compose.yml"

# Mirror sources (China-friendly, tested in order)
$MirrorList = @(
    "https://docker.m.daocloud.io"
    "https://docker.1ms.run"
)

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
    return $yml -match '(?m)^\s+erag:' -and ($yml -match 'container_name:\s*erag-lite')
}

function Test-DockerBackend {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=erag-lite" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    }
    catch { return $false }
}

# =====================================================================
# Docker mirror helpers (shared with mcp_repl.ps1 pattern)
# =====================================================================

function Get-WorkingMirrorDomain {
    <#
    .SYNOPSIS
    Returns the first working registry domain from daemon.json mirrors only.
    Respects user's daemon.json edits (add/remove/comment-out via JSON).
    Hardcoded MirrorList is NOT used here — it is only for auto-configuration.
    #>
    if (Test-Registry "https://hub.docker.com") { return "docker.io" }
    $candidates = @(Get-ExistingMirrors)
    if ($candidates.Count -eq 0) {
        Write-Host "  No mirrors in daemon.json, falling back to docker.io" -ForegroundColor DarkGray
        return "docker.io"
    }
    foreach ($m in $candidates) {
        $domain = $m -replace '^https?://', ''
        Write-Host "  Testing $domain ..." -ForegroundColor DarkGray
        if (-not (Test-Registry $m)) {
            Write-Host "    /v2/ unreachable" -ForegroundColor DarkYellow
            continue
        }
        # Test both images the Dockerfile actually pulls (multi-stage build)
        $pyOk = Test-MirrorImage -Domain $domain -Image "library/python" -Tag "3.12-slim"
        $nodeOk = Test-MirrorImage -Domain $domain -Image "library/node" -Tag "22-alpine"
        if ($pyOk -and $nodeOk) { return $domain }
        if (-not $pyOk)   { Write-Host "    python:3.12-slim blocked" -ForegroundColor DarkYellow }
        if (-not $nodeOk) { Write-Host "    node:22-alpine blocked" -ForegroundColor DarkYellow }
    }
    Write-Host "  WARNING: no daemon.json mirror can serve python:3.12-slim + node:22-alpine" -ForegroundColor Yellow
    Write-Host "           falling back to docker.io" -ForegroundColor Yellow
    return "docker.io"
}

function Test-MirrorImage {
    param([string]$Domain, [string]$Image, [string]$Tag)
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
        return ($code -eq 200 -or $code -eq 401)
    }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $code = $_.Exception.Response.StatusCode.value__
            $_.Exception.Response.Close()
            if ($code -eq 429) { return $false }
            if ($code -eq 401) { return $true }
            return ($code -eq 200)
        }
        return $false
    }
    catch { return $false }
}

function Test-Registry {
    param([string]$Url)
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

    if (Test-Registry "https://hub.docker.com") {
        Write-Host "  OK: hub.docker.com reachable" -ForegroundColor DarkGray
        return $true
    }
    Write-Host "  hub.docker.com NOT reachable" -ForegroundColor DarkYellow

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
# Kill Python processes (shared helper)
# =====================================================================

function Kill-Pythons {
    try {
        Get-Process -Name "python*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction Stop
    }
    catch {
        cmd /c "taskkill /F /IM python.exe 2>nul" | Out-Null
        cmd /c "taskkill /F /IM python3.12.exe 2>nul" | Out-Null
    }
    Start-Sleep 1
}

# =====================================================================
# Actions: Local mode
# =====================================================================

function Start-LocalBackend {
    Write-Host "=== ERAG Backend (local :$Port) ===" -ForegroundColor Cyan

    if (Test-Backend) {
        Write-Host "Backend already running on :$Port (local mode)" -ForegroundColor Yellow
        return
    }

    $env:PYTHONPATH = $BackendDir
    $env:HF_ENDPOINT = "https://hf-mirror.com"

    # Load .env
    if (Test-Path $EnvFile) {
        foreach ($line in Get-Content $EnvFile) {
            if ($line -match '^\s*([^#][^=]+)=(.*)$') {
                $k = $matches[1].Trim(); $v = $matches[2].Trim()
                [Environment]::SetEnvironmentVariable($k, $v, "Process")
            }
        }
    }

    $env:WATCHFILES_FORCE_POLLING = "true"
    Write-Host "Starting uvicorn (loading model, may take ~20s)..." -ForegroundColor Gray
    $proc = Start-Process -FilePath "py" `
        -ArgumentList "-3.12","-m","uvicorn","app.main:app","--host","127.0.0.1","--port","$Port","--reload" `
        -WorkingDirectory $Root -PassThru -WindowStyle Minimized

    Write-Host "Waiting for startup..." -NoNewline
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep 1
        if (Test-Backend) {
            Write-Host " OK (PID: $($proc.Id))" -ForegroundColor Green
            Write-Host "  Swagger: http://127.0.0.1:$Port/docs" -ForegroundColor Gray
            Write-Host "  Mode: local (no Docker)" -ForegroundColor Gray
            return
        }
        if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
    }
    Write-Host " timeout!" -ForegroundColor Red
    Write-Host "  Check manually: cd $Root && py -3.12 -m uvicorn app.main:app --port $Port" -ForegroundColor Gray
}

function Stop-LocalBackend {
    Write-Host "=== Stopping backend (local) ===" -ForegroundColor Cyan
    Kill-Pythons
    Start-Sleep 1
    if (-not (Test-Backend)) {
        Write-Host "Backend stopped" -ForegroundColor Green
    }
    else {
        Write-Host "WARNING: Backend still responding" -ForegroundColor Yellow
    }
}

# =====================================================================
# Actions: Docker mode
# =====================================================================

function Start-DockerBackend {
    Write-Host "=== ERAG Backend (Docker :$Port) ===" -ForegroundColor Cyan

    if (Test-DockerBackend) {
        Write-Host "Backend already running on :$Port (Docker mode)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-ComposeAvailable $ComposeFile)) {
        Write-Host "ERROR: docker-compose.yml missing or lacks 'erag' service" -ForegroundColor Red
        Write-Host "       Fall back: .\bin\backend.ps1 start (auto-detects Docker absence)" -ForegroundColor Yellow
        return
    }

    # Mirror check before build
    if (-not (Ensure-DockerMirror)) {
        Write-Host "Docker mirrors not ready, aborting start." -ForegroundColor Red
        Write-Host "Fix manually: .\bin\backend.ps1 fix-mirror" -ForegroundColor Yellow
        return
    }

    # Find working mirror for build-time image pull
    $buildMirror = Get-WorkingMirrorDomain
    Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror erag
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: build failed" -ForegroundColor Red
        return
    }

    Write-Host ""
    Write-Host "=== Starting container ===" -ForegroundColor Cyan
    docker compose -f $ComposeFile up -d erag
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
            Write-Host "  Swagger: http://127.0.0.1:$Port/docs" -ForegroundColor Gray
            Write-Host "  Mode: Docker container (erag-lite)" -ForegroundColor Gray
            return
        }
        if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
    }
    Write-Host " timeout!" -ForegroundColor Red
    Write-Host "  Check manually: docker logs erag-lite" -ForegroundColor Gray
}

function Stop-DockerBackend {
    Write-Host "=== Stopping backend (Docker) ===" -ForegroundColor Cyan
    if (Test-DockerBackend) {
        docker compose -f $ComposeFile stop erag
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
    Write-Host "=== ERAG Backend Status ===" -ForegroundColor Cyan
    Write-Host "  Port: $Port" -ForegroundColor Gray

    if (Test-DockerBackend) {
        Write-Host "  Mode:   Docker container" -ForegroundColor Green
        Write-Host "  Status: running (erag-lite)" -ForegroundColor Green
        $startedAt = docker inspect erag-lite --format '{{.State.StartedAt}}' 2>$null
        if ($startedAt) { Write-Host "  Since:  $startedAt" -ForegroundColor Gray }
        return
    }

    if (Test-Backend) {
        Write-Host "  Mode:   Local Python" -ForegroundColor Yellow
        Write-Host "  Status: running on :$Port" -ForegroundColor Green
        Write-Host "  Backend: $BackendDir" -ForegroundColor Gray
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
# Mirror fix
# =====================================================================

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
            Start-DockerBackend
        }
        else {
            Write-Host "[detect] Docker not found, using local mode" -ForegroundColor DarkGray
            Start-LocalBackend
        }
    }

    "stop" {
        if (Test-DockerBackend) { Stop-DockerBackend }
        else { Stop-LocalBackend }
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
        Write-Host "Rebuilding erag image (registry: $buildMirror, --no-cache) ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror --no-cache erag
    }

    "fix-mirror" { Fix-DockerMirror }

    "restart" {
        if (Test-DockerBackend) {
            Write-Host "Restarting erag-lite container (no rebuild) ..." -ForegroundColor Gray
            docker compose -f $ComposeFile restart erag
            Start-Sleep 2
            if (Test-Backend) {
                Write-Host "Backend restarted" -ForegroundColor Green
            }
            else {
                Write-Host "WARNING: Backend not responding after restart" -ForegroundColor Yellow
            }
        }
        elseif (Test-Docker) {
            Write-Host "Container not running. Use 'start' instead." -ForegroundColor Yellow
        }
        else {
            Write-Host "Backend not running in Docker mode, use 'start' for local restart" -ForegroundColor Yellow
        }
    }

    "reload" {
        if (Test-Docker) {
            if (-not (Ensure-DockerMirror)) {
                Write-Host "Mirrors not ready, run fix-mirror first" -ForegroundColor Red
                return
            }
            $buildMirror = Get-WorkingMirrorDomain
            Write-Host "=== Building (registry: $buildMirror) ===" -ForegroundColor Cyan
            docker compose -f $ComposeFile build --build-arg REGISTRY=$buildMirror erag
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: build failed" -ForegroundColor Red
                return
            }
            Write-Host ""
            Write-Host "=== Recreating container ===" -ForegroundColor Cyan
            docker compose -f $ComposeFile up -d erag
            Start-Sleep 2
            if (Test-Backend) {
                Write-Host "Backend reloaded" -ForegroundColor Green
            }
            else {
                Write-Host "WARNING: Check logs: docker logs erag-lite" -ForegroundColor Yellow
            }
        }
        else {
            Write-Host "Docker not available, use 'start' for local mode" -ForegroundColor Yellow
        }
    }

    "logs" {
        if (Test-DockerBackend) { docker logs --tail=50 -f erag-lite }
        else { Write-Host "Backend not running in Docker mode" -ForegroundColor Yellow }
    }

    default {
        Write-Host "Usage: .\bin\backend.ps1 [start|stop|restart|reload|status|build|fix-mirror|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "  start       Start backend (build + up, auto: Docker or local fallback)"
        Write-Host "  stop        Stop backend"
        Write-Host "  restart     Restart container only (no rebuild, fast)"
        Write-Host "  reload      Rebuild image + restart (for code changes)"
        Write-Host "  status      Show running status (Docker / local / not running)"
        Write-Host "  build       Rebuild Docker image only (--no-cache)"
        Write-Host "  fix-mirror  Diagnose and fix Docker registry mirrors"
        Write-Host "  logs        Tail Docker container logs"
    }
}
