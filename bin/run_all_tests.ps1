# ERAG Backend Full Test Suite
# Usage:   cd erag/backend; ..\bin\run_all_tests.ps1
#          or from erag root: .\bin\run_all_tests.ps1

$ErrorActionPreference = "Continue"

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$eragRoot  = Split-Path -Parent $scriptDir
$backend   = Join-Path $eragRoot "backend"

Set-Location $backend
$env:PYTHONPATH = "."
$env:HF_ENDPOINT = "https://hf-mirror.com"

$passed  = 0
$failed  = 0
$errors  = 0

# Accumulate all failure/xfail/summary details across batches
$failureLog = @()

Write-Host "==========================================" -ForegroundColor Cyan
Write-Host "  ERAG Backend Full Test Suite" -ForegroundColor Cyan
Write-Host "==========================================" -ForegroundColor Cyan

# ---- Install deps ----
Write-Host "`n[1/9] Installing dev dependencies (pytest, pytest-asyncio, pytest-html, httpx)..." -ForegroundColor Yellow
pip install pytest pytest-asyncio pytest-html httpx -q
Write-Host "  OK" -ForegroundColor Green

# ---- Helper: run pytest batch and tally ----
function Run-Batch($label, $files) {
    Write-Host "`n[$label] Running..." -ForegroundColor Yellow
    $output = Invoke-Expression "pytest $files -v --tb=short" 2>&1
    $output | ForEach-Object { Write-Host $_ }

    # Join output for regex matching
    $text = $output -join "`n"

    # Tally
    if ($text -match "(\d+) passed")   { $script:passed += [int]$Matches[1] }
    if ($text -match "(\d+) failed")   { $script:failed += [int]$Matches[1] }
    if ($text -match "(\d+) error")    { $script:errors += [int]$Matches[1] }

    # Capture failure/xfail lines and final summary for end-of-run report
    $batchIssues = @()
    foreach ($line in $output) {
        if ($line -match "(FAILED|XFAIL|XPASS|ERRORS)" -and $line -notmatch "=.*=") {
            $batchIssues += $line
        }
    }
    # Also capture the final count line from pytest
    $finalLine = $output | Select-String "^=+.*(passed|failed|xfailed|error|warnings).*=+" | Select-Object -Last 1
    if ($finalLine) {
        $batchIssues += $finalLine.Line
    }
    if ($batchIssues.Count -gt 0) {
        $script:failureLog += ""
        $script:failureLog += "--- [$label] ---"
        $script:failureLog += $batchIssues
    }
}

# ---- Batch 1: Unit (no BGE) ----
Run-Batch "2/9 unit (parser/chunker/bm25/cache)" "tests/unit/test_parser.py tests/unit/test_chunker.py tests/unit/test_bm25.py tests/unit/test_cache.py"

# ---- Batch 2: API auth / kb / users (no BGE) ----
Run-Batch "3/9 api (auth/kb/users)" "tests/api/test_auth.py tests/api/test_kb.py tests/api/test_users.py"

# ---- Batch 3: API retrieval (needs BGE) ----
Run-Batch "4/9 api (retrieval)" "tests/api/test_retrieval.py"

# ---- Batch 4: API documents (needs BGE) ----
Run-Batch "5/9 api (documents)" "tests/api/test_documents.py"

# ---- Batch 5: API chat (needs BGE) ----
Run-Batch "6/9 api (chat)" "tests/api/test_chat.py"

# ---- Batch 6: Security auth / rbac / conversation (no BGE) ----
Run-Batch "7/9 security (auth/rbac/conv)" "tests/security/test_auth.py tests/security/test_rbac.py tests/security/test_conversation_isolation.py"

# ---- Batch 7: Security injection (needs BGE) ----
Run-Batch "8/9 security (injection)" "tests/security/test_injection.py"

# ---- Batch 8: vector_store + integration (needs BGE) ----
Run-Batch "9/9 unit + integration (vector_store/pipeline)" "tests/unit/test_vector_store.py tests/integration/test_upload_pipeline.py"

# ---- Summary ----
Write-Host "`n==========================================" -ForegroundColor Cyan
Write-Host "  SUMMARY" -ForegroundColor Cyan
Write-Host "  Passed:  $passed" -ForegroundColor Green
if ($failed -gt 0) { Write-Host "  Failed:  $failed" -ForegroundColor Red }
if ($errors -gt 0) { Write-Host "  Errors:  $errors" -ForegroundColor Red }
Write-Host "  Total:   $($passed + $failed + $errors)" -ForegroundColor White
Write-Host "==========================================" -ForegroundColor Cyan

# ---- Failure / XFAIL detail log ----
if ($failureLog.Count -gt 0) {
    Write-Host "`n==========================================" -ForegroundColor Magenta
    Write-Host "  FAILURE / XFAIL DETAILS" -ForegroundColor Magenta
    Write-Host "==========================================" -ForegroundColor Magenta
    $failureLog | ForEach-Object { Write-Host $_ }
    Write-Host "==========================================" -ForegroundColor Magenta
}
