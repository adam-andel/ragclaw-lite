#!/usr/bin/env bash
# =====================================================================
# Shared Docker registry-mirror helpers (sourced by bin/sh/*.sh)
# =====================================================================
#
# Usage (from a script in bin/sh/):
#   source "$(dirname "$0")/lib/mirror.sh"
#   mirror="$(get_working_mirror_domain "library/python:3.12-slim" "library/node:22-alpine")"
#   [ -z "$mirror" ] && { echo "ERROR: no working mirror"; exit 1; }
#   compose build --build-arg REGISTRY="$mirror" ...
#
# get_working_mirror_domain probes candidate registries and echoes the first
# domain that (a) answers on /v2/ and (b) can actually serve every image
# the build needs (HEAD manifest, no 429 rate-limit). This avoids picking a
# mirror that is alive but rate-limited for the specific base images the
# Dockerfiles pull. Echoes the bare domain (e.g. "docker.m.daocloud.io") on
# stdout, or nothing on failure. All diagnostic output goes to stderr.
#
# Backup mirrors (China-friendly), tried only if daemon.json mirrors and
# docker.io are all unusable.

MIRROR_LIST=(
  "https://docker.m.daocloud.io"
  # "https://docker.1ms.run"
)

# Echo space-separated daemon.json registry-mirrors (honors user edits).
get_existing_mirrors() {
  local cfg="$HOME/.docker/daemon.json"
  [ -f "$cfg" ] || return 0
  grep -o '"registry-mirrors"[[:space:]]*:[[:space:]]*\[[^]]*\]' "$cfg" 2>/dev/null \
    | grep -o 'https\?://[^"]*' 2>/dev/null
}

# True if /v2/ returns 200 (no auth) or 401 (auth required).
test_registry() {
  local url="$1"
  url="${url%/}/v2/"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$url" 2>/dev/null)"
  [ "$code" = "200" ] || [ "$code" = "401" ]
}

# True if the mirror can serve <image>:<tag> manifest without a 429.
test_mirror_image() {
  local domain="$1" image="$2" tag="$3"
  local url="https://${domain}/v2/${image}/manifests/${tag}"
  local code
  code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 -I \
    -H "Accept: application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json" \
    "$url" 2>/dev/null)"
  [ "$code" = "200" ] || [ "$code" = "401" ]
}

# True only if the domain serves EVERY "image:tag" argument.
test_mirror_serves_all() {
  local domain="$1"; shift
  local spec image tag
  for spec in "$@"; do
    image="${spec%:*}"
    tag="${spec##*:}"
    if [ "$image" = "$spec" ]; then
      echo "    invalid image spec '$spec' (expected image:tag)" >&2
      return 1
    fi
    if ! test_mirror_image "$domain" "$image" "$tag"; then
      echo "    $spec unavailable" >&2
      return 1
    fi
  done
  return 0
}

# Echoes the first working registry domain, trying daemon.json mirrors first,
# then docker.io, then the hardcoded MIRROR_LIST as a last resort.
# Args: one or more "image:tag" specs. Echoes empty on total failure.
get_working_mirror_domain() {
  local required=("$@")
  local m domain

  # 1) User-configured daemon.json mirrors (respects their edits).
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    domain="${m#*://}"
    echo "  Testing $domain ..." >&2
    test_registry "$m" || { echo "    /v2/ unreachable" >&2; continue; }
    if test_mirror_serves_all "$domain" "${required[@]}"; then
      echo "$domain"
      return 0
    fi
  done < <(get_existing_mirrors)

  # 2) docker.io itself.
  echo "  WARNING: no daemon.json mirror can serve the required images, falling back to docker.io" >&2
  if test_registry "https://hub.docker.com"; then
    echo "docker.io"
    return 0
  fi
  echo "  hub.docker.com NOT reachable, using backup mirrors" >&2

  # 3) Hardcoded backup mirrors.
  for m in "${MIRROR_LIST[@]}"; do
    domain="${m#*://}"
    echo "  Testing $domain ..." >&2
    test_registry "$m" || { echo "    /v2/ unreachable" >&2; continue; }
    if test_mirror_serves_all "$domain" "${required[@]}"; then
      echo "$domain"
      return 0
    fi
  done

  echo "FAIL: no mirror reachable, check network" >&2
  return 1
}
