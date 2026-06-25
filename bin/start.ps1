# ERAG All-in-one Control Script
# Usage: .\bin\start.ps1 [start|stop|restart|status]

param([string]$Action = "start")

$BinDir = Split-Path -Parent $MyInvocation.MyCommand.Path

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
        Invoke-Script "frontend" "start"
        Invoke-Script "mcp_repl" "start"
        Write-Host ""
        Write-Host "=== All services started ===" -ForegroundColor Green
        Write-Host "  Frontend: http://localhost:5173" -ForegroundColor Gray
        Write-Host "  Backend:  http://127.0.0.1:8000/docs" -ForegroundColor Gray
        Write-Host "  REPL:     http://127.0.0.1:9200/mcp  (if enabled)" -ForegroundColor Gray
        Start-Sleep 1
        Start-Process "http://localhost:5173"
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
        Invoke-Script "frontend" "start"
        Write-Host ""
        Write-Host "=== Restart complete ===" -ForegroundColor Green
        Start-Sleep 1
        Start-Process "http://localhost:5173"
    }

    "status" {
        Write-Host "=== ERAG Service Status ===" -ForegroundColor Cyan
        Invoke-Script "mcp_repl" "status"
        Invoke-Script "backend" "status"
        Invoke-Script "frontend" "status"
    }

    default {
        Write-Host "Usage: .\bin\start.ps1 [start|stop|restart|status]" -ForegroundColor Yellow
    }
}
