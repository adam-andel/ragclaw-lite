# REGISTRY build arg: set to a mirror domain when docker.io is unreachable.
#   docker compose build --build-arg REGISTRY=docker.m.daocloud.io nginx
# APT_MIRROR build arg: Alpine apk mirror base URL (default: official
#   dl-cdn.alpinelinux.org). Pass e.g. https://mirrors.tuna.tsinghua.edu.cn to use a mirror.
#   docker compose build --build-arg APT_MIRROR=https://mirrors.tuna.tsinghua.edu.cn nginx
ARG REGISTRY=docker.io
ARG APT_MIRROR=""
FROM ${REGISTRY}/library/nginx:1.27-alpine

# inotify-tools provides inotifywait, used to hot-reload nginx when the backend
# rewrites the shared TLS material (cert/key + nginx conf) — no docker.sock.
# When APT_MIRROR is set, rewrite the Alpine repo host to the mirror first.
RUN if [ -n "$APT_MIRROR" ]; then \
      sed -i "s|https://dl-cdn.alpinelinux.org|$APT_MIRROR|g" /etc/apk/repositories; \
    fi && \
    apk add --no-cache inotify-tools

# Remove the stock site config so Docker does NOT seed the shared ragclaw_tls
# volume with nginx's default "welcome" page when the volume is first created.
# The backend materializes the real default.conf into that volume at startup.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY tls-entrypoint.sh /tls-entrypoint.sh
RUN chmod +x /tls-entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/tls-entrypoint.sh"]
