# ERAG Frontend Control Script
# Usage: .\bin\frontend.ps1 [start|stop|status]

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$FrontendDir = Join-Path $Root "frontend"

function Test-Frontend($port = 5173) {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$port" -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Kill-Nodes {
    try {
        Get-Process -Name "node" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction Stop
    } catch {
        cmd /c "taskkill /F /IM node.exe 2>nul"
    }
    Start-Sleep 1
}

switch ($Action) {
    "start" {
        Write-Host "=== Starting frontend (Vite) ===" -ForegroundColor Cyan

        # Check all possible ports (5173-5175)
        for ($p = 5173; $p -le 5175; $p++) {
            if (Test-Frontend $p) {
                Write-Host "Frontend already running on :$p" -ForegroundColor Yellow
                return
            }
        }

        # Use cmd /c for reliable PATH resolution in new process
        $cmd = "cd /d `"$FrontendDir`" && npx vite --host --port 5173"
        $proc = Start-Process -FilePath "cmd" -ArgumentList "/c", $cmd -PassThru -WindowStyle Minimized

        Write-Host "Waiting for startup..." -NoNewline
        for ($i = 0; $i -lt 45; $i++) {
            Start-Sleep 1
            # Check ports 5173-5175
            $found = $false
            for ($p = 5173; $p -le 5175; $p++) {
                if (Test-Frontend $p) {
                    Write-Host " OK on :$p" -ForegroundColor Green
                    Write-Host "  URL: http://localhost:$p" -ForegroundColor Gray
                    $found = $true
                    break
                }
            }
            if ($found) { return }
            if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
        }
        Write-Host " timeout!" -ForegroundColor Red
        Write-Host "  Try manually: cd frontend && npx vite --host --port 5173" -ForegroundColor Gray
    }

    "stop" {
        Write-Host "=== Stopping frontend ===" -ForegroundColor Cyan
        Kill-Nodes
        Start-Sleep 1
        Write-Host "Frontend stopped" -ForegroundColor Green
    }

    "status" {
        $found = $false
        for ($p = 5173; $p -le 5175; $p++) {
            if (Test-Frontend $p) {
                Write-Host "Frontend: running on :$p" -ForegroundColor Green
                $found = $true
                break
            }
        }
        if (-not $found) { Write-Host "Frontend: not running" -ForegroundColor Red }
    }

    default {
        Write-Host "Usage: .\bin\frontend.ps1 [start|stop|status]" -ForegroundColor Yellow
    }
}
