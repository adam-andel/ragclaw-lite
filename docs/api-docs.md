# API Docs / API 文档

## Swagger UI

After starting, visit Swagger UI at the nginx entry point (host port is randomly assigned by Docker by default, unless `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT` is set in `.env`):

- The startup script prints the real URL, e.g. `http://localhost:<actual-port>/docs` (or `https://localhost:<actual-port>/docs` when HTTPS is enabled)
- Or query manually: `docker compose -f docker-compose.yml port nginx 80` (and `... port nginx 443` when HTTPS is enabled)

## New endpoints (v0.5.0)

| Endpoint | Notes |
|----------|-------|
| `GET/POST/DELETE /api/workspace/*` | 【Claw】workspace file management (list / read-write / upload / download / zip) |
| `POST/GET /api/skills` | SKILL management |
| `PATCH/DELETE /api/skills/{id}` | SKILL edit/delete |
| `POST/DELETE /api/skills/{id}/tools` | Tool bind/unbind |
| `POST/GET /api/mcp/servers` | MCP Server management |
| `PATCH/DELETE /api/mcp/servers/{id}` | MCP Server edit/delete |
| `POST /api/mcp/servers/{id}/test` | MCP connection test |
| `POST /api/chat/stream` | New `skill_id` param |
| `GET / PUT /api/config/https` | HTTPS settings (enable + cert/key); nginx hot-reloads on change |