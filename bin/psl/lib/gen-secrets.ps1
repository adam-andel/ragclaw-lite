# =====================================================================
# Generate Docker secret files for RAGClaw if they are missing.
#
# Faithful PowerShell counterpart of bin/sh/lib/gen-secrets.sh. Dot-source this
# AFTER $Root is defined in the calling script (it reads $Root directly, the
# same scoping convention used by lib/common.ps1 and lib/mirror.ps1).
#
# These files are mounted into the containers as read-only files at
# /run/secrets/<name> (see the top-level `secrets:` block in docker-compose.yml
# and the service `secrets:` references). They are gitignored and must be backed
# up — losing ragclaw_config_key makes the encrypted config.enc (LLM / embedding
# API keys) undecryptable.
#
# Files produced (32 random bytes, stored as 64 hex chars):
#   secrets/ragclaw_config_key   AES-256 key for config.enc
# Note: the JWT signing secret and the REPL identity secret are now DB-backed
# (auto-generated on first boot, rotated via the admin UI), so they are NOT
# generated as mounted secret files.
#
# Windows notes (intentional divergences from the bash original):
#   * No /dev/urandom: use the OS crypto RNG (RandomNumberGenerator), which is
#     always present in PowerShell / .NET and cryptographically strong. openssl
#     is NOT assumed to be on PATH on Windows.
#   * Output is exactly 64 ASCII bytes (no BOM, no trailing newline) via
#     Set-Content -Encoding ASCII -NoNewline, matching `openssl rand -hex 32`.
#   * chmod 600 has no Windows equivalent; a best-effort ACL tighten grants the
#     current user FullControl without stripping inherited rules. Non-fatal.
# =====================================================================

function Initialize-RagclawSecrets {
    $dir = Join-Path $Root "secrets"
    if (-not (Test-Path $dir)) {
        New-Item -ItemType Directory -Path $dir -Force | Out-Null
    }

    $names = @("ragclaw_config_key")
    $missing = $false
    foreach ($n in $names) {
        if (-not (Test-Path (Join-Path $dir $n))) {
            $missing = $true
            break
        }
    }
    if (-not $missing) {
        return
    }

    Write-Host "=== Generating Docker secrets (first run) ===" -ForegroundColor DarkGray

    foreach ($n in $names) {
        $f = Join-Path $dir $n
        if (Test-Path $f) {
            continue
        }

        # 32 random bytes -> 64 hex chars, via the OS crypto RNG.
        $bytes = New-Object byte[] 32
        $rng = [System.Security.Cryptography.RandomNumberGenerator]::Create()
        $rng.GetBytes($bytes)
        $hex = ($bytes | ForEach-Object { $_.ToString("x2") }) -join ""

        Set-Content -Path $f -Value $hex -NoNewline -Encoding ASCII

        # chmod 600 is a no-op on NTFS; best-effort tighten so the current user
        # can read/write the secret. Never breaks first-run setup on failure.
        try {
            $user = if ($env:USERDOMAIN) { "$env:USERDOMAIN\$env:USERNAME" } else { $env:USERNAME }
            $acl = Get-Acl $f
            $rule = New-Object System.Security.AccessControl.FileSystemAccessRule(
                $user, "FullControl", "Allow")
            $acl.AddAccessRule($rule)
            Set-Acl $f $acl
        } catch { }

        Write-Host "  generated secret: $n" -ForegroundColor DarkGray
    }
}
