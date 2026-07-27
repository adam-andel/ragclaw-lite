#!/usr/bin/env bash
# Generate Docker secret files for RAGClaw if they are missing.
#
# These files are mounted into the containers as read-only files at
# /run/secrets/<name> (see the top-level `secrets:` block in docker-compose.yml
# and the service `secrets:` references). They are gitignored (bin/sh/.gitignore
# / .gitignore has `secrets/`) and must be backed up — losing ragclaw_config_key
# makes the encrypted config.enc (LLM / embedding API keys) undecryptable.
#
# Files produced (each 32 random bytes, stored as 64 hex chars):
#   secrets/ragclaw_config_key   AES-256 key for config.enc
#   secrets/ragclaw_jwt_secret  JWT HS256 signing secret

gen_secrets() {
  local dir="$ROOT/secrets"
  mkdir -p "$dir"
  local names=("ragclaw_config_key" "ragclaw_jwt_secret")
  local missing=0
  for n in "${names[@]}"; do
    if [ ! -f "$dir/$n" ]; then
      missing=1
      break
    fi
  done
  if [ "$missing" -eq 0 ]; then
    return 0
  fi

  c_dim "=== Generating Docker secrets (first run) ==="
  for n in "${names[@]}"; do
    local f="$dir/$n"
    if [ -f "$f" ]; then
      continue
    fi
    if command -v openssl >/dev/null 2>&1; then
      openssl rand -hex 32 > "$f" 2>/dev/null
    fi
    # Fallback if openssl is unavailable or produced nothing.
    if [ ! -s "$f" ]; then
      head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n' > "$f"
    fi
    chmod 600 "$f"
    c_dim "  generated secret: $n"
  done
}
