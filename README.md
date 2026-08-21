# RAGClaw

> A shared agent platform for private deployment in small & mid-sized organizations

[![GitHub Release](https://img.shields.io/github/v/release/adam-andel/ragclaw-lite?style=flat-square&label=Release)](https://github.com/adam-andel/ragclaw-lite/releases)

**[简体中文](./README_zh.md) | English**

---

## What is RAGClaw

RAGClaw is a shared agent platform for private deployment in mid-size organizations, defined by three key traits:

**【Ease of use】** With Docker already installed, a single `menu` script deploys the whole platform on one machine;

**【Security】** Data and keys remain fully private — API keys are centrally managed and never land on disk or leak;

**【Controllability】** LLM access, SKILL, MCP Server, and the sandbox network policy are all governed by a super administrator, so the capability boundary is set by your organization.

---

## 🔑 Core Features

1. **Fine-grained context management**
   - **Multi-level compaction** (rolling summary → RAG archive chain) keeps more of a long conversation in memory, while **async compaction** never blocks answer generation — cutting first-token latency (TTFT); memory can be **recalled via hybrid retrieval** (vector + BM25); and you can still **edit or delete it manually**, fully under your control.

2. **Hardened execution sandbox**
   - Claw's code runs in a separate `mcp-repl` container on an internal-only network, with per-user UID isolation, a brokered network-egress policy, and container hardening (`read_only` + `seccomp` + `cap_drop`) — safe to "let it act". *(deep dive: 🧱 The Sandbox below)*

3. **Shared, co-managed workspace**
   - a local-folder-like file space you and the agent manage together: breadcrumb nav, list/grid views, drag-drop upload, batch zip, and recursive search, all backed by the same volume the sandbox writes to. *(deep dive: 📁 The Workspace below)*

4. **Efficient & secure shared SKILL system**
   - upload a whole skill folder (or zip) in the management page — everything lands at once, easy to use. A configurable `.ragclaw/` adapter boosts script-invocation efficiency while leaving the third-party source untouched. API-key skills route through an **injection proxy**, so skills can be shared across multiple users without exposing the API key.

5. **RAG + BM25 hybrid retrieval**
   - hybrid retrieval runs **vector search and BM25 in parallel**, and the LLM can **proactively invoke the `hybrid_search` meta-tool on demand** — topping up a sparse first-pass recall; for references like "these meetings" / "them" it resolves them against conversation history (**coreference resolution**) and rewrites the query to be self-contained before searching.

---

## 📦 Installation

### Prerequisites
- **Docker** Engine 20.10+ on Linux, or **Docker Desktop** on Windows / macOS (enable the WSL2 backend on Windows for development mode).
- **Docker Compose v2** (`docker compose`). It ships with Docker Desktop; on Linux install the `docker-compose-plugin`.
- **openssl** on the host — generates the AES key for `config.enc` on first run (falls back to `/dev/urandom` if absent).
- **(Windows)** For development mode, run from a **WSL2** distro, not a `C:\` path, because bind mounts forwarded via 9P are slow and break inotify hot-reload. See [Development Mode](#-development-mode-hot-reload) below.

### 1. Get the code
```bash
git clone https://github.com/adam-andel/ragclaw-lite ragclaw-lite
cd ragclaw-lite
```

### 2. Configure environment
`.env` is **optional** — the stack starts fine without it, using built-in defaults (the three ports get a random host mapping; the project name falls back to the directory name). Copy the template and edit only when you want to pin ports or change LLM settings:
```bash
cp .env.example .env
```
Key variables (all documented inline in `.env.example`):

| Variable | Default when unset | Purpose |
|----------|--------------------|---------|
| `COMPOSE_PROJECT_NAME` | empty (dir name) | Prefix for all container / network / volume names; use a distinct value per clone |
| `RAGCLAW_HTTP_PORT` | random mapping | Host port for the nginx HTTP entry |
| `RAGCLAW_HTTPS_PORT` | random mapping | Host port for HTTPS (only used when TLS is enabled in Settings) |
| `RAGCLAW_FRONTEND_PORT` | random mapping | Host port for the Vite HMR dev server (dev mode) |
| `RAGCLAW_INTERNAL_NET` | `172.30.0` | Sandbox subnet; must differ for every parallel clone |

### 3. Secrets (auto-generated)
On first start `bin/sh/start.sh` / the menu auto-generates `secrets/ragclaw_config_key` (the AES-256 key that decrypts `config.enc`) if missing. Create it manually if you prefer:
```bash
openssl rand -hex 32 > secrets/ragclaw_config_key
chmod 600 secrets/ragclaw_config_key
```
> ⚠️ Back this file up. Losing it makes the encrypted API keys in `config.enc` unrecoverable.

### 4. Build & start (production)
The bundled **control menu** drives the whole "build images → start containers" flow — pick a number, or run the one-liner:
```bash
# macOS / Linux
bash bin/sh/start.sh start        # or: bash bin/sh/menu.sh (interactive)
# Windows (CMD)
bin\psl\start.bat
```
> Note: the Chinese menu (`menu_zh.sh` / `menu_zh.bat`) configures non-official mirror sources (Docker / APT / pip) by default to speed up pulls — verify they match your network and security policy before use.

The first run builds all images (`ragclaw`, `mcp-repl`, `ragclaw-egress`, `nginx`); later `reload` reuses them without rebuilding.

### 5. Access & first-time setup
- The startup script prints the actual entry URL (random host port when not pinned); Swagger UI is at **/docs**.
- Open the app, complete the **initial super-admin setup**, then go to **Settings → LLM** and set/paste your API key (encrypted into `config.enc`).
- Optional: enable **HTTPS** and upload a certificate in Settings — nginx hot-reloads on change.

---

## 🛠️ Development Mode (hot-reload)

For daily development, use the **dev mode** — source changes take effect instantly, no repeated image rebuilds.

### Why run it inside WSL2

Docker Desktop uses the WSL2 backend. If the project lives on the Windows host, bind mounts are forwarded to the Linux VM via 9P/gRPC-FUSE, making file I/O noticeably slower. Placing the project inside a WSL2 distro's filesystem (native ext4) mounts directly, with near-local performance and the best hot-reload experience.

---

## 📂 Project Structure

See [docs/project-structure.md](docs/project-structure.md) for the full directory tree.

---

## 🛠️ Tech Stack

See [docs/tech-stack.md](docs/tech-stack.md) for the full stack table.

---

## 📝 API Docs

See [docs/api-docs.md](docs/api-docs.md) for Swagger access and the endpoint table.

---

## 🤝 Contributing

Contributions are welcome. The full guide is in [CONTRIBUTING.md](docs/contributing-guide.md). Highlights:

- **Open an issue first** for any substantial change (new feature, architecture, breaking change), then PR against `main`.
- **Commit messages are English**, `type(scope): subject` — e.g. `fix(chat): handle zero-quota on continue`. See the type table in the guide.
- **Keep PRs small and focused**; never stage `.env`, secrets, or unrelated files.
- **Code style**: comments in English; keep `bin/psl` (Windows) and `bin/sh` (macOS/Linux) script parity.
- **Tests run in container mode only** — `bash bin/sh/run_all_tests.sh`. Pay special attention to `security/` cases (auth, RBAC, isolation, injection, IDOR).
- **Security-sensitive areas** (sandbox, egress policy, API-key injection, skill adapters, user isolation) must have their security impact described in the PR.

By contributing you agree to license your work under **Apache License 2.0**.

---

## 📬 Contact & Support

For bug reports and feature requests, please [open an issue](https://github.com/adam-andel/ragclaw-lite/issues)

---

## 📄 License

Apache License 2.0 — see the [LICENSE](LICENSE) file for details.
