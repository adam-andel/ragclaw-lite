# The Workspace

> A shared, local-folder-like file space that **you and the agent manage together**.

Most agents hand you nothing but a download link. RAGClaw gives you a **real workspace** — a file browser that feels like your OS file manager, and, crucially, **it is the very same workspace the Claw writes to**. The agent can create, run, and produce files there; you can open, rename, move, edit, or delete them — and the agent sees your changes too. Two-way, full control, no export-and-import dance.

## Why it feels like a local folder

- **Native browsing.** Breadcrumb navigation, **list *and* grid (card) views**, type filters (Office / PDF / images / archives / JSON…), and eight sort options (name / time / size / type). Recursive filename search reaches into subfolders and tells you when results are truncated.
- **Everything a file manager does.** Create folders, upload via **drag-and-drop with a concurrent pool** that shows per-file progress and supports pause / resume / cancel, download single files or a whole selection as one ZIP, rename, move (a directory picker that refuses to drop a folder into itself), and batch delete.

## Co-managed with the agent

Because the workspace is shared with the sandbox, what you drop in becomes instantly available to the agent, and what the agent generates shows up right in your folder — ready to grab, tweak, or ship. It's the connective tissue between "the agent did something" and "you can actually use the result."

> Backed by the same `ragclaw_workspace` volume the sandbox uses, so your files **survive restarts** and stay **isolated per user**. The frontend `WorkspaceView.vue` is the UI; the `/api/workspace/*` endpoints (backend `routers/workspace.py`) are the same API the agent calls.