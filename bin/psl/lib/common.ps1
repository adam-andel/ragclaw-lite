# =====================================================================
# Shared helpers for bin/psl/*.ps1 — dot-source AFTER $Root/$ComposeFile are set.
#
# Faithful PowerShell counterpart of bin/sh/lib/common.sh. Centralizes the
# cross-script Docker / egress / compose helpers so backend.ps1, mcp_repl.ps1,
# run_all_tests.ps1 and start.ps1 share ONE definition instead of copying it.
# Also exposes the ragclaw published-port resolver (Get-RagclawPublishedPort)
# and project-name resolver (Get-ProjectName).
# =====================================================================

# ---- Resolve the actual host port the ragclaw service is published on ----
# Mirrors bin/sh/lib/common.sh::ragclaw_published_port.
# Handles all three cases uniformly: inline RAGCLAW_PORT, .env RAGCLAW_PORT, or a
# random ephemeral port. Requires the container to be up (call after `up`).
# `docker compose port ragclaw 8000` prints e.g. "0.0.0.0:8000" or ":::8000";
# if it cannot be resolved yet (container not running), fall back to 8000.
function Get-RagclawPublishedPort {
    $out = docker compose -f $ComposeFile port ragclaw 8000 2>$null
    if ($out) {
        $port = ($out -split ':' | Select-Object -Last 1).Trim()
        if ($port -match '^\d+$') { return $port }
    }
    return 8000
}

# ---- Resolve the Compose project name the SAME way `docker compose` does ----
# ($COMPOSE_PROJECT_NAME > .env COMPOSE_PROJECT_NAME= > directory basename) and
# return the project-scoped container-name prefix, e.g. "dev-egress" for a
# COMPOSE_PROJECT_NAME=dev instance. The bash counterpart is proj_name() in
# bin/sh/lib/common.sh. Used so these scripts work for ANY instance, not just
# the default "ragclaw" project.
function Get-ProjectName {
    $p = $env:COMPOSE_PROJECT_NAME
    if (-not $p) {
        $envFile = Join-Path $Root ".env"
        if (Test-Path $envFile) {
            $envLine = Select-String -Path $envFile -Pattern '^COMPOSE_PROJECT_NAME=' | Select-Object -First 1
            if ($envLine) { $p = ($envLine.Line -replace '^COMPOSE_PROJECT_NAME=').Trim() }
        }
    }
    if (-not $p) { $p = Split-Path -Leaf $Root }
    return $p
}

# =====================================================================
# Shared Docker / compose / egress helpers (centralized from the individual
# bin/psl/*.ps1 scripts so backend.ps1, mcp_repl.ps1, run_all_tests.ps1 and
# start.ps1 share ONE definition instead of copying it).
# =====================================================================

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

function Repair-EgressNetwork {
    <#
    .SYNOPSIS
    Frees the fixed egress IP on the internal network so a subsequent
    `docker compose up` does not fail with "Address already in use".
    .PARAMETER ForceNetwork
    Also removes the internal network itself (releasing the stuck IPAM
    lease left behind by a prior `down`). Used on the retry attempt when a
    stale container alone was not the cause.
    #>
    param([switch]$ForceNetwork)

    if (-not (Test-Docker)) { return }

    # Resolve the project name the SAME way compose does, then derive the
    # egress container name ("{project}ragclaw-egress"). Never hardcode "ragclaw-egress"
    # — a second instance (COMPOSE_PROJECT_NAME=dev) would otherwise never match.
    $proj = Get-ProjectName
    $egressName = "${proj}ragclaw-egress"

    # 1) Remove any stale (non-running) egress broker container holding the IP.
    $egressId = docker ps -a -q -f "name=$egressName" 2>$null
    if ($egressId) {
        $running = docker ps -q -f "name=$egressName" 2>$null
        if (-not $running) {
            Write-Host "  Removing stale $egressName container to free its fixed IP..." -ForegroundColor DarkGray
            docker compose -f $ComposeFile rm -f ragclaw-egress 2>$null | Out-Null
        }
    }

    if (-not $ForceNetwork) { return }

    # 2) Force-remove the egress broker and then the internal network, releasing
    #    the daemon's IPAM lease. The internal network is named by compose as
    #    {project}_ragclaw-internal. IMPORTANT: {project}ragclaw-lite (backend) and
    #    mcp-repl are NORMAL members — we must NOT `docker rm -f` them (that would
    #    kill the live backend). Delete ONLY the egress broker; merely
    #    `disconnect` every other attached container; `up` then reconnects them.
    $net = "$proj`_ragclaw-internal"
    $attached = docker network inspect $net --format '{{range $k,$v := .Containers}}{{$k}}{{"\n"}}{{end}}' 2>$null
    if ($attached) {
        $attached | ForEach-Object {
            if (-not $_) { return }
            $name = (docker inspect --format '{{.Name}}' $_ 2>$null) -replace '^/',''
            if ($name -eq $egressName -or $_ -eq $egressId) {
                docker rm -f $_ 2>$null | Out-Null                            # the broken broker — safe to delete
            } else {
                docker network disconnect -f $net $_ 2>$null | Out-Null       # keep the container, just detach
            }
        }
    }
    docker network rm $net 2>$null | Out-Null
    Write-Host "  Released $net network IPAM lease; will recreate on up." -ForegroundColor DarkGray
}

function Test-ComposeAvailable {
    param(
        [string]$ComposePath,
        [string]$Service        # compose service to verify, e.g. "ragclaw" or "mcp-repl"
    )
    if (-not (Test-Path $ComposePath)) { return $false }
    $yml = Get-Content $ComposePath -Raw
    # Service block present — matches the backend "ragclaw" service and the
    # REPL "mcp-repl" service. Callers pass $Service so a single definition
    # serves both scripts (previously duplicated with divergent guards).
    # NOTE: use ${Service} to delimit the variable name — "$Service:" would be
    # parsed as a PSDrive reference (like $env:Foo) and fail to compile.
    return $yml -match "(?m)^\s*${Service}:"
}
