FROM nginx:1.27-alpine

# inotify-tools provides inotifywait, used to hot-reload nginx when the backend
# rewrites the shared TLS material (cert/key + nginx conf) — no docker.sock.
RUN apk add --no-cache inotify-tools

# Remove the stock site config so Docker does NOT seed the shared ragclaw_tls
# volume with nginx's default "welcome" page when the volume is first created.
# The backend materializes the real default.conf into that volume at startup.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY tls-entrypoint.sh /tls-entrypoint.sh
RUN chmod +x /tls-entrypoint.sh

EXPOSE 443

ENTRYPOINT ["/tls-entrypoint.sh"]
