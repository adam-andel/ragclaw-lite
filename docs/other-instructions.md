# Other Instructions

## Deployment entry point (nginx)
In production, all inbound traffic reaches the app through the **nginx** reverse proxy — the only published entry point. The backend listens on `:8000` only inside the internal `ragclaw` network (not published to the host). nginx terminates TLS when HTTPS is enabled (toggle it on **Settings → HTTPS**, paste a cert + key, and nginx hot-reloads), and otherwise serves plain HTTP. See the `nginx/` directory and `RAGCLAW_HTTP_PORT` / `RAGCLAW_HTTPS_PORT` in `.env.example`.

## ⚠️ Single-worker hard constraint
The backend runs uvicorn with exactly **one worker** (`--workers 1`). RAGClaw relies on process-local singletons — the LLM concurrency semaphore, the in-memory BM25 index, the answer cache, the per-process Chroma client — that must **not** be forked across multiple workers. Never override the command with `--workers N`; to scale, add more containers (horizontal), not more workers per container (vertical).