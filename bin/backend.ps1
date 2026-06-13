# ERAG Backend Control Script
# Usage: .\bin\backend.ps1 [start|stop|status]

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$BackendDir = Join-Path $Root "backend"
$EnvFile = Join-Path $Root ".env"

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

function Test-Backend {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

function Kill-Pythons {
    try {
        Get-Process -Name "python*" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction Stop
    } catch {
        cmd /c "taskkill /F /IM python.exe 2>nul" | Out-Null
        cmd /c "taskkill /F /IM python3.12.exe 2>nul" | Out-Null
    }
    Start-Sleep 1
}

switch ($Action) {
    "start" {
        Write-Host "=== Starting backend (FastAPI :8000) ===" -ForegroundColor Cyan

        if (Test-Backend) {
            Write-Host "Backend already running on :8000" -ForegroundColor Yellow
            return
        }

        $env:WATCHFILES_FORCE_POLLING = "true"
        $proc = Start-Process -FilePath "py" `
            -ArgumentList "-3.12","-m","uvicorn","app.main:app","--host","127.0.0.1","--port","8000","--reload" `
            -WorkingDirectory $Root -PassThru -WindowStyle Minimized

        Write-Host "Waiting for startup (loading model, may take ~20s)..." -NoNewline
        for ($i = 0; $i -lt 60; $i++) {
            Start-Sleep 1
            if (Test-Backend) {
                Write-Host " OK (PID: $($proc.Id))" -ForegroundColor Green
                Write-Host "  Swagger: http://127.0.0.1:8000/docs" -ForegroundColor Gray
                return
            }
            if ($i % 5 -eq 4) { Write-Host "." -NoNewline }
        }
        Write-Host " timeout!" -ForegroundColor Red
        Write-Host "  Check manually: cd $Root && py -3.12 -m uvicorn app.main:app --port 8000" -ForegroundColor Gray
    }

    "stop" {
        Write-Host "=== Stopping backend ===" -ForegroundColor Cyan
        Kill-Pythons
        Write-Host "Backend stopped" -ForegroundColor Green
    }

    "status" {
        if (Test-Backend) {
            Write-Host "Backend: running on :8000" -ForegroundColor Green
        } else {
            Write-Host "Backend: not running" -ForegroundColor Red
        }
    }

    default {
        Write-Host "Usage: .\bin\backend.ps1 [start|stop|status]" -ForegroundColor Yellow
    }
}
