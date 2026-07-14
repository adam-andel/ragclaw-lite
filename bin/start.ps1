# ERAG All-in-one Control Script
# Usage: .\bin\start.ps1 [start|stop|restart|status]
#
# Container mode only: the backend runs containerized and serves the frontend
# from the container — no local Vite / local Python is used. This project must
# run in container mode.

param([string]$Action = "start")

$BinDir = Split-Path -Parent $MyInvocation.MyCommand.Path

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
        Assert-Docker
        Invoke-Script "backend" "start"
        Invoke-Script "mcp_repl" "start"
        Write-Host ""
        Write-Host "=== All services started (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App:     http://localhost:8000" -ForegroundColor Gray
        Write-Host "  Swagger: http://127.0.0.1:8000/docs" -ForegroundColor Gray
        Write-Host "  REPL:    http://127.0.0.1:9200/mcp  (if enabled)" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process "http://localhost:8000"
    }

    "stop" {
        Invoke-Script "backend" "stop"
        Invoke-Script "mcp_repl" "stop"
        Write-Host ""
        Write-Host "=== All services stopped ===" -ForegroundColor Green
    }

    "restart" {
        Write-Host "=== Restarting all services ===" -ForegroundColor Cyan
        Invoke-Script "backend" "stop"
        Invoke-Script "mcp_repl" "stop"
        Start-Sleep 2
        Invoke-Script "mcp_repl" "start"
        Invoke-Script "backend" "start"
        Write-Host ""
        Write-Host "=== Restart complete (Docker mode) ===" -ForegroundColor Green
        Write-Host "  App: http://localhost:8000" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process "http://localhost:8000"
    }

    "status" {
        Write-Host "=== ERAG Service Status ===" -ForegroundColor Cyan
        Write-Host "  Mode: Docker container" -ForegroundColor Cyan
        Invoke-Script "mcp_repl" "status"
        Invoke-Script "backend" "status"
        Write-Host "Frontend: served by container (port 8000)" -ForegroundColor Green
    }

    default {
        Write-Host "Usage: .\bin\start.ps1 [start|stop|restart|status]" -ForegroundColor Yellow
    }
}
