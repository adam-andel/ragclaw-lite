# =====================================================================
# Shared helpers for bin/psl/*.ps1 — dot-source AFTER setting $ComposeFile.
#
# Faithful PowerShell counterpart of bin/sh/lib/common.sh. Currently exposes
# the erag published-port resolver so every script reports the REAL host port
# (inline ERAG_PORT > .env ERAG_PORT > random), not a hardcoded :8000.
# =====================================================================

# ---- Resolve the actual host port the erag service is published on ----
# Mirrors bin/sh/lib/common.sh::erag_published_port.
# Handles all three cases uniformly: inline ERAG_PORT, .env ERAG_PORT, or a
# random ephemeral port. Requires the container to be up (call after `up`).
# `docker compose port erag 8000` prints e.g. "0.0.0.0:8000" or ":::8000";
# if it cannot be resolved yet (container not running), fall back to 8000.
function Get-EragPublishedPort {
    $out = docker compose -f $ComposeFile port erag 8000 2>$null
    if ($out) {
        $port = ($out -split ':' | Select-Object -Last 1).Trim()
        if ($port -match '^\d+$') { return $port }
    }
    return 8000
}
