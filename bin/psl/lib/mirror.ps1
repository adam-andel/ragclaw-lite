# =====================================================================
# Shared Docker registry-mirror helpers (dot-sourced by the bin/psl/*.ps1 scripts)
# =====================================================================
#
# Usage (from a script in bin/psl/):
#   . (Join-Path $PSScriptRoot "lib\mirror.ps1")
#   $mirror = Get-WorkingMirrorDomain -RequiredImages @(
#       "library/python:3.12-slim", "library/node:22-alpine")
#   if (-not $mirror) { return }   # no reachable mirror
#   docker compose build --build-arg REGISTRY=$mirror
#
# Get-WorkingMirrorDomain probes candidate registries and returns the first one
# that (a) answers on /v2/ and (b) can actually serve every image the build
# needs (HEAD manifest, no 429 rate-limit). This avoids picking a mirror that is
# alive but rate-limited for the specific base images the Dockerfiles pull.

# Backup mirrors (China-friendly), tried only if daemon.json mirrors and
# docker.io are all unusable.
$MirrorList = @(
    "https://docker.m.daocloud.io"
    # "https://docker.1ms.run"
)

function Get-ExistingMirrors {
    <#
    .SYNOPSIS
    Returns the registry-mirrors array from the user's ~/.docker/daemon.json,
    honoring their edits (add/remove/comment-out via JSON). Empty if none.
    #>
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

function Test-Registry {
    <#
    .SYNOPSIS
    True if the Docker Registry API v2 endpoint is alive. /v2/ returns 200
    (no auth) or 401 (auth required) — both mean the registry is reachable.
    Other codes or connection errors mean unreachable.
    #>
    param([string]$Url)
    $testUrl = $Url.TrimEnd('/') + "/v2/"
    try {
        $req = [System.Net.HttpWebRequest]::Create($testUrl)
        $req.UserAgent = "RAGCLAW-Mirror-Checker"
        $req.Timeout = 5000
        $req.AllowAutoRedirect = $true
        $req.Method = "GET"
        $resp = $req.GetResponse()
        $code = $resp.StatusCode.value__
        $resp.Close()
        return ($code -eq 200 -or $code -eq 401)
    }
    catch [System.Net.WebException] {
        # WebException still carries the response on protocol errors
        if ($_.Exception.Response) {
            $code = $_.Exception.Response.StatusCode.value__
            $_.Exception.Response.Close()
            return ($code -eq 200 -or $code -eq 401)
        }
        return $false
    }
    catch { return $false }
}

function Test-MirrorImage {
    <#
    .SYNOPSIS
    True if the mirror can serve the given image manifest without a 429
    rate-limit. 200 = found, 401 = needs auth but registry is alive.
    #>
    param([string]$Domain, [string]$Image, [string]$Tag)
    $url = "https://$Domain/v2/$Image/manifests/$Tag"
    try {
        $req = [System.Net.HttpWebRequest]::Create($url)
        $req.UserAgent = "RAGCLAW-Mirror-Checker"
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
            if ($code -eq 429) { return $false }  # rate-limited
            if ($code -eq 401) { return $true }   # needs auth, but reachable
            return ($code -eq 200)
        }
        return $false
    }
    catch { return $false }
}

function Test-MirrorServesAll {
    <#
    .SYNOPSIS
    True only if the domain can serve EVERY required image. Each spec is
    "<image>:<tag>", e.g. "library/python:3.12-slim". Prints which image is
    missing so the caller's log stays informative.
    #>
    param([string]$Domain, [string[]]$RequiredImages)
    foreach ($spec in $RequiredImages) {
        $idx = $spec.LastIndexOf(':')
        if ($idx -lt 0) {
            Write-Host "    invalid image spec '$spec' (expected image:tag)" -ForegroundColor DarkYellow
            return $false
        }
        $image = $spec.Substring(0, $idx)
        $tag   = $spec.Substring($idx + 1)
        if (-not (Test-MirrorImage -Domain $Domain -Image $image -Tag $tag)) {
            Write-Host "    $spec unavailable" -ForegroundColor DarkYellow
            return $false
        }
    }
    return $true
}

function Get-WorkingMirrorDomain {
    <#
    .SYNOPSIS
    Returns the first working registry domain, trying daemon.json mirrors first,
    then docker.io, then the hardcoded $MirrorList as a last resort. A mirror is
    "working" only if it serves every image in -RequiredImages. Returns $null if
    no mirror is reachable.
    .PARAMETER RequiredImages
    Image specs the build needs, as "<image>:<tag>" strings. Defaults to just
    python:3.12-slim (the common case for the python-only services).
    #>
    param(
        [string[]]$RequiredImages = @("library/python:3.12-slim")
    )

    # 1) User-configured daemon.json mirrors (respects their edits).
    foreach ($m in @(Get-ExistingMirrors)) {
        $domain = $m -replace '^https?://', ''
        Write-Host "  Testing $domain ..." -ForegroundColor DarkGray
        if (-not (Test-Registry $m)) {
            Write-Host "    /v2/ unreachable" -ForegroundColor DarkYellow
            continue
        }
        if (Test-MirrorServesAll -Domain $domain -RequiredImages $RequiredImages) { return $domain }
    }

    # 2) docker.io itself.
    Write-Host "  WARNING: no daemon.json mirror can serve the required images, falling back to docker.io" -ForegroundColor DarkYellow
    if (Test-Registry "https://hub.docker.com") { return "docker.io" }
    Write-Host "  hub.docker.com NOT reachable, using backup mirrors" -ForegroundColor DarkYellow

    # 3) Hardcoded backup mirrors.
    foreach ($m in $MirrorList) {
        $domain = $m -replace '^https?://', ''
        Write-Host "  Testing $domain ..." -ForegroundColor DarkGray
        if (-not (Test-Registry $m)) {
            Write-Host "    /v2/ unreachable" -ForegroundColor DarkYellow
            continue
        }
        if (Test-MirrorServesAll -Domain $domain -RequiredImages $RequiredImages) { return $domain }
    }

    Write-Host "FAIL: no mirror reachable, check network" -ForegroundColor Red
    return $null
}
