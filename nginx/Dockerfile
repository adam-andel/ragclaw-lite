# REGISTRY build arg: set to a China mirror domain when docker.io is unreachable
#   docker compose build --build-arg REGISTRY=docker.m.daocloud.io nginx
ARG REGISTRY=docker.io
FROM ${REGISTRY}/library/nginx:1.27-alpine

# inotify-tools provides inotifywait, used to hot-reload nginx when the backend
# rewrites the shared TLS material (cert/key + nginx conf) — no docker.sock.
# Try the official Alpine repos first; fall back to a domestic mirror when
# dl-cdn.alpinelinux.org is unreachable (consistent with the apt/pip fallback
# pattern used in the other images).
RUN apk add --no-cache inotify-tools || \
    (sed -i 's|https://dl-cdn.alpinelinux.org|https://mirrors.tuna.tsinghua.edu.cn|g' /etc/apk/repositories && \
     apk add --no-cache inotify-tools)

# Remove the stock site config so Docker does NOT seed the shared ragclaw_tls
# volume with nginx's default "welcome" page when the volume is first created.
# The backend materializes the real default.conf into that volume at startup.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY tls-entrypoint.sh /tls-entrypoint.sh
RUN chmod +x /tls-entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/tls-entrypoint.sh"]
