# RAGClaw

> A shared agent (Agent) platform for private deployment in mid-size organizations

---

## What is RAGClaw

RAGClaw is a shared agent (Agent) platform for private deployment in mid-size organizations, defined by three key traits:

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

## 🚀 Quick Start

**Prerequisite:** install Docker first — Docker Desktop on Windows / macOS, or Docker Engine on Linux. See the official installation guide at <https://docs.docker.com/get-docker/>.

Once Docker is ready, the no-fuss way to deploy is the bundled **control menu script** — an interactive menu that drives the whole "build images → start containers" flow; just pick a number.

```bash
# macOS / Linux
bash bin/sh/menu.sh            # Chinese UI: bash bin/sh/menu_zh.sh

# Windows (double-click / run in CMD)
bin\psl\menu.bat               # Chinese UI: bin\psl\menu_zh.bat
```
---

## 🛠️ Development Mode (hot-reload)

For daily development, use the **dev mode** — source changes take effect instantly, no repeated image rebuilds.

### Why run it inside WSL2

Docker Desktop uses the WSL2 backend. If the project lives on the Windows host, bind mounts are forwarded to the Linux VM via 9P/gRPC-FUSE, making file I/O noticeably slower. Placing the project inside a WSL2 distro's filesystem (native ext4) mounts directly, with near-local performance and the best hot-reload experience.
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

Contributions are welcome. Please open an issue to discuss substantial changes first, then submit a PR against `main`. For commit conventions, code style, testing requirements, and security-sensitive areas, see [CONTRIBUTING.md](docs/contributing-guide.md).

---

## 📬 Contact & Support

For bug reports and feature requests, please [open an issue](https://github.com/你的用户名/你的项目名/issues)

---

## 📄 License

Apache License 2.0 — see the [LICENSE](LICENSE) file for details.
