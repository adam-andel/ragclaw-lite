# ERAG All-in-one Control Script
# Usage: .\bin\start.ps1 [start|stop|restart|status]
#
# Smart mode: auto-detects Docker. If Docker is available, backend runs
# containerized and serves frontend from the container — no local Vite needed.
# If Docker is not available, falls back to local Python + Vite dev server.

param([string]$Action = "start")

$BinDir = Split-Path -Parent $MyInvocation.MyCommand.Path

function Test-Docker {
    try { $null = docker --version 2>$null; return ($LASTEXITCODE -eq 0) }
    catch { return $false }
}

function Test-DockerBackend {
    if (-not (Test-Docker)) { return $false }
    try {
        $id = docker ps -q -f "name=erag-lite" 2>$null
        return ($id -and $LASTEXITCODE -eq 0)
    }
    catch { return $false }
}

function Invoke-Script($name, $action) {
    $path = Join-Path $BinDir "$name.ps1"
    if (Test-Path $path) {
        & $path $action
    } else {
        Write-Host "Script not found: $path" -ForegroundColor Red
    }
}

switch ($Action) {
    "start" {
        Invoke-Script "backend" "start"
        Invoke-Script "mcp_repl" "start"

        $dockerMode = Test-DockerBackend

        if ($dockerMode) {
            Write-Host ""
            Write-Host "=== All services started (Docker mode) ===" -ForegroundColor Green
            Write-Host "  App:     http://localhost:8000" -ForegroundColor Gray
            Write-Host "  Swagger: http://127.0.0.1:8000/docs" -ForegroundColor Gray
            Write-Host "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)" -ForegroundColor Gray
            Start-Sleep 1
            Start-Process "http://localhost:8000"
        } else {
            Invoke-Script "frontend" "start"
            Write-Host ""
            Write-Host "=== All services started (local mode) ===" -ForegroundColor Green
            Write-Host "  Frontend: http://localhost:5173" -ForegroundColor Gray
            Write-Host "  Backend:  http://127.0.0.1:8000/docs" -ForegroundColor Gray
            Write-Host "  REPL:     http://127.0.0.1:9200/mcp  (if enabled)" -ForegroundColor Gray
            Start-Sleep 1
            Start-Process "http://localhost:5173"
        }
    }

    "stop" {
        Invoke-Script "frontend" "stop"
        Invoke-Script "backend" "stop"
        Invoke-Script "mcp_repl" "stop"
        Write-Host ""
        Write-Host "=== All services stopped ===" -ForegroundColor Green
    }

    "restart" {
        Write-Host "=== Restarting all services ===" -ForegroundColor Cyan
        Invoke-Script "frontend" "stop"
        Invoke-Script "backend" "stop"
        Invoke-Script "mcp_repl" "stop"
        Start-Sleep 2
        Invoke-Script "mcp_repl" "start"
        Invoke-Script "backend" "start"

        if (Test-DockerBackend) {
            Write-Host ""
            Write-Host "=== Restart complete (Docker mode) ===" -ForegroundColor Green
            Write-Host "  App: http://localhost:8000" -ForegroundColor Gray
            Start-Sleep 1
            Start-Process "http://localhost:8000"
        } else {
            Invoke-Script "frontend" "start"
            Write-Host ""
            Write-Host "=== Restart complete (local mode) ===" -ForegroundColor Green
            Start-Sleep 1
            Start-Process "http://localhost:5173"
        }
    }

    "status" {
        Write-Host "=== ERAG Service Status ===" -ForegroundColor Cyan
        $dockerMode = Test-DockerBackend
        if ($dockerMode) {
            Write-Host "  Mode: Docker container" -ForegroundColor Cyan
        } else {
            Write-Host "  Mode: Local" -ForegroundColor Cyan
        }
        Invoke-Script "mcp_repl" "status"
        Invoke-Script "backend" "status"
        if (-not $dockerMode) {
            Invoke-Script "frontend" "status"
        } else {
            Write-Host "Frontend: served by container (port 8000)" -ForegroundColor Green
        }
    }

    default {
        Write-Host "Usage: .\bin\start.ps1 [start|stop|restart|status]" -ForegroundColor Yellow
    }
}
