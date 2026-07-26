#!/bin/sh
# nginx TLS entrypoint.
#
# The backend writes `default.conf` (plus fullchain.pem / privkey.pem when
# HTTPS is enabled) into the shared ragclaw_tls volume, which is mounted here
# at /etc/nginx/conf.d (read-only). This entrypoint waits for that config to
# appear, validates it, starts nginx, then hot-reloads whenever the shared
# directory changes (inotify) — so cert/key or enable/disable changes take
# effect without restarting the container.

CONF_DIR=/etc/nginx/conf.d
CONF="$CONF_DIR/default.conf"

echo "[nginx-tls] waiting for $CONF ..."
while [ ! -f "$CONF" ]; do
  sleep 0.5
done

echo "[nginx-tls] validating nginx config"
nginx -t || echo "[nginx-tls] WARNING: config validation failed; nginx may not start"

echo "[nginx-tls] starting nginx"
nginx -g 'daemon off;' &
NGINX_PID=$!

reload() {
  echo "[nginx-tls] config changed, reloading nginx"
  nginx -s reload 2>/dev/null || echo "[nginx-tls] reload failed"
}

trap 'echo "[nginx-tls] shutting down"; nginx -s quit 2>/dev/null; wait $NGINX_PID' TERM INT

echo "[nginx-tls] watching $CONF_DIR for changes"
inotifywait -m -e modify -e create -e delete "$CONF_DIR" | while read -r _; do
  reload
done

wait $NGINX_PID
