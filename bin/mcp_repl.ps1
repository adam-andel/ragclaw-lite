# ERAG Python REPL MCP Server Control Script
# Usage: .\bin\mcp_repl.ps1 [start|stop|status|build]
#
# Smart mode: auto-detects Docker. If Docker is installed → runs containerized.
# If Docker is not installed → falls back to local Python venv.
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

# Ensure workspace exists (used by both modes)
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null }

# ── helpers ────────────────────────────────────────────────

function Test-Docker {
    try {
        $null = docker --version 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch { return $false }
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
    } catch { return $false }
}

function Test-LocalRepl {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method Post `
            -Headers @{"Content-Type" = "application/json"} `
            -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' `
            -TimeoutSec 3 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Start-LocalRepl {
    Write-Host "=== Python REPL MCP Server (local :$Port) ===" -ForegroundColor Cyan

    if (Test-LocalRepl) {
        Write-Host "REPL server already running on :$Port (local)" -ForegroundColor Yellow
        return
    }

    # Setup venv if missing
    if (-not (Test-Path $VenvDir)) {
        Write-Host "Creating venv at $VenvDir ..." -ForegroundColor Gray
        py -3.12 -m venv $VenvDir
        if ($LASTEXITCODE -ne 0) {
            Write-Host "ERROR: Failed to create venv (is Python 3.12 installed?)" -ForegroundColor Red
            return
        }
        Write-Host "Installing packages (pandas, python-docx, python-pptx, PyPDF2) ..." -ForegroundColor Gray
        & "$VenvDir\Scripts\python.exe" -m pip install --quiet pandas python-docx python-pptx PyPDF2
        Write-Host "Venv ready" -ForegroundColor Green
    }

    if (-not (Test-Path $ServerScript)) {
        Write-Host "ERROR: $ServerScript not found" -ForegroundColor Red
        return
    }

    Write-Host "Starting REPL server (allow-dir: $WorkDir) ..." -ForegroundColor Gray
    $proc = Start-Process -FilePath "$VenvDir\Scripts\python.exe" `
        -ArgumentList $ServerScript, "--port", $Port, "--allow-dir", $WorkDir, "--no-network", "--keep-minutes", "120" `
        -WorkingDirectory $McpDir -PassThru -WindowStyle Minimized

    Start-Sleep 3
    if (Test-LocalRepl) {
        Write-Host "REPL server started (PID: $($proc.Id))" -ForegroundColor Green
        Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
        Write-Host "  Workspace: $WorkDir" -ForegroundColor Gray
        Write-Host "  Mode: local (no Docker)" -ForegroundColor Gray
    } else {
        Write-Host "WARNING: REPL server may not have started, check console window" -ForegroundColor Yellow
    }
}

function Start-DockerRepl {
    Write-Host "=== Python REPL MCP Server (Docker :$Port) ===" -ForegroundColor Cyan

    if (Test-DockerRepl) {
        Write-Host "REPL server already running on :$Port (Docker)" -ForegroundColor Yellow
        return
    }

    if (-not (Test-ComposeAvailable $ComposeFile)) {
        Write-Host "ERROR: docker-compose.yml missing or lacks mcp-repl service. Fall back to local mode:" -ForegroundColor Red
        Write-Host "       .\bin\mcp_repl.ps1 start  (will auto-detect no docker and use local)" -ForegroundColor Yellow
        return
    }

    # Ensure image is built
    Write-Host "Building mcp-repl image ..." -ForegroundColor Gray
    docker compose -f $ComposeFile build mcp-repl
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose build failed" -ForegroundColor Red
        return
    }

    Write-Host "Starting mcp-repl container ..." -ForegroundColor Gray
    docker compose -f $ComposeFile up -d mcp-repl
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: docker compose up failed" -ForegroundColor Red
        return
    }

    Start-Sleep 3
    if (Test-LocalRepl) {
        Write-Host "REPL server started (Docker)" -ForegroundColor Green
        Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
        Write-Host "  Workspace: Docker volume 'erag_workspace' → /app/workspace" -ForegroundColor Gray
        Write-Host "  Mode: Docker container (erag-mcp-repl)" -ForegroundColor Gray
        Write-Host "  Resources: memory=1G, cpus=2 (configurable in docker-compose.yml)" -ForegroundColor Gray
    } else {
        Write-Host "WARNING: Container started but not responding, check logs: docker logs erag-mcp-repl" -ForegroundColor Yellow
    }
}

function Stop-LocalRepl {
    Write-Host "=== Stopping REPL server (local) ===" -ForegroundColor Cyan
    try {
        Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object {
            $_.CommandLine -like "*python_repl_mcp_server*"
        } | Stop-Process -Force -ErrorAction SilentlyContinue
    } catch { }
    Start-Sleep 1
    if (-not (Test-LocalRepl)) {
        Write-Host "REPL server stopped" -ForegroundColor Green
    } else {
        Write-Host "WARNING: REPL server still responding, may need manual kill" -ForegroundColor Yellow
    }
}

function Stop-DockerRepl {
    Write-Host "=== Stopping REPL server (Docker) ===" -ForegroundColor Cyan
    if (Test-DockerRepl) {
        docker compose -f $ComposeFile stop mcp-repl
        if ($LASTEXITCODE -eq 0) {
            Write-Host "REPL server stopped (Docker container)" -ForegroundColor Green
        }
    } else {
        Write-Host "REPL server not running (Docker)" -ForegroundColor Yellow
    }
}

function Show-Status {
    Write-Host "=== Python REPL MCP Server Status ===" -ForegroundColor Cyan
    Write-Host "  Port: $Port" -ForegroundColor Gray

    # Check Docker first
    if (Test-DockerRepl) {
        Write-Host "  Mode:   Docker container" -ForegroundColor Green
        Write-Host "  Status: running (erag-mcp-repl)" -ForegroundColor Green
        $info = docker inspect erag-mcp-repl --format '{{.State.StartedAt}}' 2>$null
        if ($info) { Write-Host "  Since:  $info" -ForegroundColor Gray }
        return
    }

    # Then check local
    if (Test-LocalRepl) {
        Write-Host "  Mode:   Local Python" -ForegroundColor Yellow
        Write-Host "  Status: running on :$Port (workspace: $WorkDir)" -ForegroundColor Green
        return
    }

    # Neither
    if (Test-Docker) {
        Write-Host "  Mode:   Docker available (but not running)" -ForegroundColor Yellow
    } else {
        Write-Host "  Mode:   Local only (Docker not installed)" -ForegroundColor Yellow
    }
    Write-Host "  Status: NOT running" -ForegroundColor Red
}

# ── dispatch ───────────────────────────────────────────────

switch ($Action) {
    "start" {
        if (Test-Docker) {
            Write-Host "[detect] Docker found → container mode" -ForegroundColor DarkGray
            Start-DockerRepl
        } else {
            Write-Host "[detect] Docker not found → local mode" -ForegroundColor DarkGray
            Start-LocalRepl
        }
    }

    "stop" {
        if (Test-DockerRepl) {
            Stop-DockerRepl
        } else {
            Stop-LocalRepl
        }
    }

    "status" {
        Show-Status
    }

    "build" {
        # Rebuild Docker image only (useful after code changes)
        if (-not (Test-Docker)) {
            Write-Host "ERROR: Docker not available" -ForegroundColor Red
            return
        }
        Write-Host "Rebuilding mcp-repl Docker image ..." -ForegroundColor Gray
        docker compose -f $ComposeFile build --no-cache mcp-repl
    }

    "logs" {
        if (Test-DockerRepl) {
            docker logs --tail=50 -f erag-mcp-repl
        } else {
            Write-Host "REPL server not running in Docker mode" -ForegroundColor Yellow
        }
    }

    default {
        Write-Host "Usage: .\bin\mcp_repl.ps1 [start|stop|status|build|logs]" -ForegroundColor Yellow
        Write-Host ""
        Write-Host "Actions:" -ForegroundColor Gray
        Write-Host "  start   Start REPL server (auto: Docker → local fallback)" -ForegroundColor Gray
        Write-Host "  stop    Stop REPL server (auto-detect mode)" -ForegroundColor Gray
        Write-Host "  status  Show running status and mode" -ForegroundColor Gray
        Write-Host "  build   Rebuild Docker image (--no-cache)" -ForegroundColor Gray
        Write-Host "  logs    Tail Docker container logs" -ForegroundColor Gray
    }
}
