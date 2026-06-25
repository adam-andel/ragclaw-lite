# ERAG Python REPL MCP Server Control Script
# Usage: .\bin\mcp_repl.ps1 [start|stop|status]
#
# Creates an isolated venv with pandas, python-docx, python-pptx, PyPDF2.
# The REPL subprocess is restricted to $Root\data\workspace.

param([string]$Action = "start")

$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$McpDir = Join-Path $Root "mcp"
$VenvDir = Join-Path $McpDir "venv"
$ServerScript = Join-Path $McpDir "python_repl_mcp_server.py"
$WorkDir = "$Root\data\workspace"
$Port = 9200

# Ensure workspace exists
if (-not (Test-Path $WorkDir)) { New-Item -ItemType Directory -Path $WorkDir -Force | Out-Null }

function Test-Repl {
    try {
        $r = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/mcp" -Method Post `
            -Headers @{"Content-Type" = "application/json"} `
            -Body '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' `
            -TimeoutSec 2 -ErrorAction SilentlyContinue
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

switch ($Action) {
    "start" {
        Write-Host "=== Python REPL MCP Server (:$Port) ===" -ForegroundColor Cyan

        if (Test-Repl) {
            Write-Host "REPL server already running on :$Port" -ForegroundColor Yellow
            return
        }

        # Setup venv if missing
        if (-not (Test-Path $VenvDir)) {
            Write-Host "Creating venv at $VenvDir ..." -ForegroundColor Gray
            py -3.12 -m venv $VenvDir
            if ($LASTEXITCODE -ne 0) {
                Write-Host "ERROR: Failed to create venv" -ForegroundColor Red
                return
            }

            Write-Host "Installing packages (pandas, python-docx, python-pptx, PyPDF2) ..." -ForegroundColor Gray
            & "$VenvDir\Scripts\python.exe" -m pip install --quiet pandas python-docx python-pptx PyPDF2
            Write-Host "Venv ready" -ForegroundColor Green
        }

        # Verify server script exists
        if (-not (Test-Path $ServerScript)) {
            Write-Host "ERROR: $ServerScript not found" -ForegroundColor Red
            return
        }

        Write-Host "Starting REPL server (allow-dir: $WorkDir) ..." -ForegroundColor Gray
        $proc = Start-Process -FilePath "$VenvDir\Scripts\python.exe" `
            -ArgumentList $ServerScript, "--port", $Port, "--allow-dir", $WorkDir, "--no-network", "--keep-minutes", "120" `
            -WorkingDirectory $McpDir -PassThru -WindowStyle Minimized

        Start-Sleep 2
        if (Test-Repl) {
            Write-Host "REPL server started (PID: $($proc.Id))" -ForegroundColor Green
            Write-Host "  Endpoint: http://127.0.0.1:$Port/mcp" -ForegroundColor Gray
            Write-Host "  Workspace: $WorkDir" -ForegroundColor Gray
        } else {
            Write-Host "WARNING: REPL server may not have started, check logs" -ForegroundColor Yellow
        }
    }

    "stop" {
        Write-Host "=== Stopping REPL server ===" -ForegroundColor Cyan
        try {
            Get-Process -Name "python*" -ErrorAction SilentlyContinue | Where-Object {
                $_.CommandLine -like "*python_repl_mcp_server*"
            } | Stop-Process -Force -ErrorAction SilentlyContinue
        } catch { }
        Start-Sleep 1
        if (-not (Test-Repl)) {
            Write-Host "REPL server stopped" -ForegroundColor Green
        }
    }

    "status" {
        if (Test-Repl) {
            Write-Host "REPL server: running on :$Port (workspace: $WorkDir)" -ForegroundColor Green
        } else {
            Write-Host "REPL server: not running" -ForegroundColor Red
        }
    }

    default {
        Write-Host "Usage: .\bin\mcp_repl.ps1 [start|stop|status]" -ForegroundColor Yellow
    }
}
