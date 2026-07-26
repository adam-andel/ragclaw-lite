FROM nginx:1.27-alpine

# inotify-tools provides inotifywait, used to hot-reload nginx when the backend
# rewrites the shared TLS material (cert/key + nginx conf) — no docker.sock.
RUN apk add --no-cache inotify-tools

COPY tls-entrypoint.sh /tls-entrypoint.sh
RUN chmod +x /tls-entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/tls-entrypoint.sh"]
