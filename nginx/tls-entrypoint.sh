#!/bin/sh
# nginx TLS entrypoint.
#
# The backend writes `default.conf` (plus fullchain.pem / privkey.pem when
# HTTPS is enabled) into the shared ragclaw_tls volume, mounted here at
# /etc/nginx/conf.d. This entrypoint ensures nginx can ALWAYS start and serve,
# even before the backend has written its config, and hot-reloads whenever the
# shared file changes (inotify).
#
# Why the fallback + runtime resolver matter:
#   * nginx resolves proxy_pass upstreams at CONFIG-LOAD time. If the backend
#     container ("ragclaw") is not yet registered in Docker DNS when nginx
#     starts, a static `proxy_pass http://ragclaw:8000` makes nginx fail fatally
#     ("host not found in upstream") and the whole site entry goes down. Because
#     the backend depends_on nginx, nginx ALWAYS starts first — so we MUST use a
#     runtime resolver (127.0.0.11 = Docker embedded DNS) + a variable upstream,
#     which defers name resolution to request time.
#   * The shared volume may already hold a config from a previous run (possibly
#     invalid, e.g. an old static-upstream conf). If `nginx -t` rejects it we
#     write a fallback reverse-proxy config (same runtime-resolver pattern) so
#     nginx binds its port immediately instead of refusing to start. The backend
#     overwrites default.conf with the real config later; inotify then reloads.

CONF_DIR=/etc/nginx/conf.d
CONF="$CONF_DIR/default.conf"

# Make the shared volume writable for the non-root backend before it writes.
chmod 777 "$CONF_DIR" 2>/dev/null || true

write_fallback_conf() {
  # Minimal reverse proxy that resolves ragclaw at request time, so nginx starts
  # even if the backend container is not yet registered in Docker DNS.
  cat > "$CONF" <<'EOF'
server {
    listen 80;
    server_name _;
    resolver 127.0.0.11 valid=10s ipv6=off;
    set $backend_upstream ragclaw:8000;
    location / {
        proxy_pass http://$backend_upstream;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_read_timeout 3600s;
    }
}
EOF
}

# Guarantee a config nginx can start with: if the shared volume already holds an
# invalid config (stale static upstream from a prior run), overwrite it with the
# fallback. A valid config is left untouched for the backend to refine later.
if ! nginx -t 2>/dev/null; then
  echo "[nginx-tls] existing config invalid, writing fallback reverse-proxy config"
  write_fallback_conf
fi

start_nginx() {
  if nginx -t 2>/dev/null; then
    nginx -g 'daemon off;' &
    return 0
  fi
  echo "[nginx-tls] config invalid, nginx not started"
  return 1
}

echo "[nginx-tls] starting nginx"
start_nginx

echo "[nginx-tls] watching $CONF_DIR for changes"
inotifywait -m -e modify -e create -e delete "$CONF_DIR" | while read -r _; do
  # Let the writer finish; avoid testing a half-written file.
  sleep 0.3
  if nginx -t 2>/dev/null; then
    # Graceful reload if a master is running; otherwise (re)start nginx.
    if nginx -s reload 2>/dev/null; then
      echo "[nginx-tls] config changed, reloaded nginx"
    else
      echo "[nginx-tls] config changed, (re)starting nginx"
      nginx -g 'daemon off;' &
    fi
  else
    echo "[nginx-tls] config invalid, skipping reload"
  fi
done
