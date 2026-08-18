# ragclaw Skill Installation Guide

> Applies to: ragclaw dev stack (`ragclaw-lite` + `ragclaw-mcp-repl` + `ragclaw-egress`)
> Audience: users who need to install, configure, or troubleshoot third-party skills in ragclaw.

---

## 0. Scope & Terminology

### 0.1 What this document covers

- What a skill package looks like, and how to assess which category it belongs to before installing;
- The four install / update / delete operations;
- Post-install configuration that may be needed: internet egress, API KEY, adapter;
- Verification methods and a common-failure reference.

### 0.2 Core terminology

| Term | Meaning |
|------|---------|
| **Skill package** | A directory: `SKILL.md` (required) + optional `scripts/`, optional `.ragclaw/`, etc. |
| **store/** | The canonical single source of truth for skills (shared volume `/ragclaw_skills/store`); written by the backend, read-only to the sandbox |
| **enable/** | The set of enable symlinks (`enable/<skill> -> ../store/<skill>`) that decides whether a skill is enabled |
| **.ragclaw/** | The ragclaw adapter directory (optional). It holds ragclaw-owned adaptation code, **not** third-party sources |
| **adapter.json** | The adapter manifest: declares the CLI module, endpoint attribute, KEY env var, upstream API URL |
| **shim.py** | The adapter core: imports the third-party CLI, redirects its endpoint to the injection proxy, strips the KEY env var |
| **Injection proxy** | `:9090` inside the ragclaw-egress container; holds API KEYs in process memory and injects `Authorization` before forwarding outbound |
| **Egress network policy** | The sandbox outbound allowlist, configured under Settings → Network Policy; hot-reloaded on save |
| **upstream** | The real API URL a CLI talks to (e.g. `https://api.anysearch.com`) |

---

## 1. Anatomy of a Skill Package

### 1.1 Typical directory layout (the factory-preset anysearch as an example)

```
anysearch/
├── SKILL.md                 # Required: capability description + frontmatter
├── README.md / LICENSE / SECURITY.md ...   # Attachments, optional, no effect on install
├── .env.example             # The third party's own KEY example, for reference only
├── runtime.conf.example     # Third-party runtime template (anysearch-specific, see 4.4)
├── scripts/                 # Optional: third-party CLI scripts (multi-platform)
│   ├── anysearch_cli.py
│   ├── anysearch_cli.sh
│   └── anysearch_cli.js
└── .ragclaw/                # Optional: ragclaw adapter (fixed directory name)
    ├── adapter.json         # Adapter manifest
    ├── shim.py              # Endpoint redirection + KEY stripping
    ├── SKILL.ragclaw.md     # Extra notes injected for the LLM (never touches the third-party SKILL.md)
    └── init.sh              # Optional init hook (runs once on enable/update)
```

### 1.2 SKILL.md (required)

Installation is enforced: **a package without `SKILL.md` cannot be uploaded** (both folder and zip uploads are validated).

The frontmatter at the top of `SKILL.md` is the key to judging a skill's capabilities:

```yaml
---
name: anysearch
description: Real-time search engine supporting web search, vertical domain search...
version: 3.0.1
credentials:
  - name: ANYSEARCH_API_KEY
    required: false
    description: "API key for higher rate limits. Anonymous access available..."
    storage: ".env file, environment variable, or --api_key CLI flag"
---
```

- `name` / `description`: registered into the decision context; this is what the LLM uses to recognize and select the skill;
- `credentials`: declares whether the skill uses an API KEY and whether it is required. **This is the primary signal for deciding whether a KEY must be configured.**

### 1.3 The `.ragclaw/` adapter (optional, but mandatory for "scripted + internet + KEY" skills)

| File | Purpose |
|------|---------|
| `adapter.json` | See field details below. Its presence declares "this skill goes through the injection proxy" |
| `shim.py` | At runtime, rewrites the CLI's endpoint attribute to the injection proxy URL and strips the KEY env var from the process |
| `SKILL.ragclaw.md` | ragclaw-injected supplementary instructions for the LLM (e.g. which command to use, output format rules); the third-party SKILL.md is left untouched |
| `init.sh` | Optional init hook, executed once by the backend on enable or update (see 4.4) |

`adapter.json` fields (anysearch as an example):

```json
{
  "cli_module": "anysearch_cli",      // Third-party CLI module to import
  "endpoint_attr": "ENDPOINT",        // Module attribute holding the endpoint to rewrite
  "endpoint_env": null,               // Whether to override the endpoint via env var (null = no)
  "key_env": "ANYSEARCH_API_KEY",     // Name of the KEY env var the third-party CLI reads
  "proxy_path": "anysearch",          // Route prefix on the injection proxy (globally unique)
  "endpoint_suffix": "/mcp",          // Real upstream sub-path (appended when forwarding)
  "header_format": "Bearer {}",       // Authorization header format
  "upstream_base": "https://api.anysearch.com"   // Real upstream API URL
}
```

> For a skill with `adapter.json`: the LLM runs `.ragclaw/shim.py` in the sandbox (not the bare CLI); requests are intercepted by the injection proxy `:9090`, which attaches the KEY held in memory and forwards upstream. The sandbox never sees a real KEY.

### 1.4 `scripts/` (optional)

Third-party CLI scripts, invoked by the LLM via `run_shell` / `run_python` according to the docs. **The system never parses script sources** — usage is always defined by `SKILL.md` (and the `.ragclaw` notes).

---

## 2. Pre-install Assessment: Which Category Does This Skill Fall Into

Answer three questions before installing:

1. **Does it have `scripts/`?** — decides whether the skill is "script-driven" or "knowledge-driven";
2. **Does it need internet access?** — check whether `SKILL.md`'s Overview/Trigger mentions APIs, online data, search, etc.;
3. **Does it need an API KEY?** — check the frontmatter `credentials` (`required: true/false`) and whether a real KEY is configured.

There are five combinations, each with different post-install actions:

| Category | Has scripts | Needs internet | Needs KEY | Post-install action |
|----------|:---:|:---:|:---:|---------------------|
| **① No script · local-only** | No | No | No | **Ready to use after install, zero configuration** |
| **② No script · needs internet** | No | Yes | — | **Needs egress network policy allowlist** (see 4.1) |
| **③ Scripted · local-only** | Yes | No | No | Ready to use, zero config; a `.ragclaw` adapter is **optional** to improve command discovery |
| **④ Scripted · internet · no KEY** | Yes | Yes | No | Two paths: egress allowlist for direct calls, **or** a `.ragclaw` adapter via the injection proxy (recommended — KEY-ready for later) |
| **⑤ Scripted · internet · with KEY** | Yes | Yes | Yes | A `.ragclaw` adapter **must** be written to route through the injection proxy, plus configure the KEY in Settings |

### Per-category details

**① No script · local-only** (e.g. local code analysis, formatting, document generation)
- Only `SKILL.md`; no scripts, no network requests. The LLM executes directly from the knowledge in `SKILL.md`.
- Enable after upload and it works — no extra configuration.

**② No script · needs internet**
- No bundled scripts, but `SKILL.md` guides the LLM to visit external sites/APIs (e.g. "check the xx official website").
- The sandbox blocks direct internet by default; you must add the target domains to the network policy allowlist first.

**③ Scripted · local-only**
- Scripts only do local processing (files, computation, conversion) — no outbound traffic.
- Ready to use after install. **Optional**: write a `.ragclaw` adapter (notably `SKILL.ragclaw.md` stating "which command, which arguments") so the LLM lands on the right invocation on the first try instead of trial and error.

**④ Scripted · internet · no KEY** (e.g. anysearch anonymous mode)
- Either path:
  - **Path A (egress allowlist)**: allowlist the domains the scripts hit, and the LLM runs the scripts directly via `run_shell`;
  - **Path B (`.ragclaw` adapter, recommended)**: write an adapter that routes through the injection proxy, which forwards anonymously. Benefits: the LLM locates the command in one step via `SKILL.ragclaw.md` / `runtime.conf`, and configuring a KEY later requires zero script changes.

**⑤ Scripted · internet · with KEY** (e.g. search/query skills backed by a private API)
- **There is no alternative — it must go through the injection proxy**: by design, a real KEY exists in only two places — the encrypted store `config.enc` and the injection proxy's process memory; the sandbox never holds a real KEY. A script connecting directly to the internet therefore cannot carry a KEY; the only path is `.ragclaw` adapter → injection proxy → proxy injects the KEY and forwards upstream.
- After install, configure the KEY for this skill in Settings (see 4.2).

---

## 3. Installation Steps

### 3.1 Prepare the skill package

- The directory must contain `SKILL.md` at its root, with complete frontmatter;
- The directory name should match the `name` in `SKILL.md` (lowercase letters / digits / hyphens; avoid Chinese characters and spaces);
- Keep all third-party files (`scripts/`, `README`, etc.) untouched;
- To route through the injection proxy, add the `.ragclaw/` directory (adapter.json + shim.py + SKILL.ragclaw.md; refer to the factory-preset anysearch package).

### 3.2 Method 1: folder upload

1. Open the **Skills** management page in the left sidebar;
2. Click "Upload folder", select **all files** in the skill directory (relative paths preserved);
3. After submission the system automatically: writes to `store/` → creates the enable symlink → runs `init.sh` (if present) → registers the injection-proxy upstream (if `adapter.json` is present).

### 3.3 Method 2: zip upload

1. Zip the skill directory — the zip **must have exactly one top-level directory** (that directory's name is the skill name), otherwise the upload is rejected;
2. Choose "Upload zip" on the Skills page;
3. Everything after that is identical to folder upload.

### 3.4 Updating / re-uploading

- Re-uploading an existing skill (reupload folder / zip) **replaces the whole directory** and clears the script cache;
- Upstream registration on the injection proxy is re-run afterwards as well;
- It is recommended to disable the skill before re-uploading to avoid concurrent use during the update.

### 3.5 Enable / disable

- The toggle on the Skills page is enable/disable: enabling creates the `enable/<skill>` symlink, disabling removes it;
- Only **enabled** skills appear in the chat skill selector;
- Disabling does not touch the files in `store/`; you can re-enable at any time.

### 3.6 Deletion

- Deleting removes: the `store/` directory, the enable symlink, the upstream mapping and KEY in the injection proxy, and the DB record;
- A factory-preset skill that is deleted will be **re-seeded on the next backend restart** (see 3.7); third-party skills disappear for good.

### 3.7 Factory presets (seeds)

- The image ships a set of factory skills (`backend/seeds/skills/`; currently anysearch);
- On first boot (empty shared volume) they are copied into `store/` and enabled automatically;
- **Idempotent**: skills already present in `store/` are never overwritten by seeding — your modifications/deletions survive restarts; re-seeding only happens when the shared volume has been wiped.

---

## 4. Post-install Configuration

### 4.1 Internet egress (egress network policy)

Applies to: categories **② ④** (no adapter; scripts that need to connect directly).

1. Open **Settings → Network Policy**;
2. Set the mode to **allowlist**, and add the domains the scripts need to reach (e.g. `api.xxx.com`) under "Allowed domains";
3. Saving takes effect immediately — the policy is hot-reloaded into the sandbox (`PUT /policy`), **no container restart needed**.

> The three modes: `deny` (block everything, default), `allow` (allow everything, not recommended), `allowlist` (only allowlisted domains). Skills routed through the injection proxy (those with adapter.json) are not constrained by this policy — they only reach registered upstreams, see 4.3.

### 4.2 Configuring an API KEY

Applies to: category **⑤** (and category ④ once a KEY is configured later).

- **Settings is the only entry point for configuring an API KEY** (no longer read from `.env`);
- Find the KEY input for this skill in Settings, fill it in and save;
- On save, the backend pushes `{folder, proxy_path, upstream_base, header_format, api_key}` to the injection proxy (`PUT /secret`); the KEY lives only in `config.enc` and the proxy's memory;
- Clearing the KEY also clears it from the proxy's memory (back to anonymous mode).

### 4.3 Automatic upstream registration on the injection proxy

For any skill **with `adapter.json`**, its `proxy_path → upstream_base` mapping is pushed to the injection proxy automatically (`PUT /secret-config`, no KEY needed) at these moments:

- after a successful upload / re-upload;
- after configuring or clearing an API KEY;
- on every backend startup (full scan of `store/` as a safety net).

So normally no manual intervention is required; if you see an "unregistered skill proxy path" error, it is typically an exception outside these moments (see the failure table in 5.3).

### 4.4 The `init.sh` hook and `runtime.conf`

- `.ragclaw/init.sh` is a **generic optional hook**: any skill that ships it is executed once by the backend on "enable" or "re-upload" (idempotent; failure does not break installation). It is for skill-specific initialization, such as detecting the CLI platform or generating a runtime config.
- **Note: `runtime.conf` is a private artifact of the factory anysearch adapter, generated by its `init.sh`** (filling the detected runtime and command into `runtime.conf.example`). **It is not required for, nor a generic requirement of, every skill.** Do not assume "every skill must have a runtime.conf".
- If your skill has no `init.sh`, skip it — it does not affect installation.

---

## 5. Verification & Common Failures

### 5.1 Functional verification checklist

- [ ] The skill shows as "enabled" on the Skills page;
- [ ] It is selectable in the chat skill selector;
- [ ] Start a new conversation (to avoid stale context), select the skill, and run one typical task;
- [ ] Expected result: **the first tool call succeeds** and an answer is produced;
- [ ] For KEY-backed skills: confirm requests actually go through the injection proxy (see log verification).

### 5.2 Log verification

| Container | What to look at | Command |
|-----------|-----------------|---------|
| backend | upload / registration / KEY-push records | `docker logs ragclaw-lite` |
| egress | upstream registration, whether forwarding happens | `docker logs ragclaw-egress` |
| mcp-repl | the actual commands run_shell executes in the sandbox | `docker logs ragclaw-mcp-repl` |

After installing/configuring an adapter-bearing skill, `docker logs ragclaw-egress` should show something like
`registered upstream config folder=anysearch`.

### 5.3 Common failure reference

| Symptom | Meaning | Fix |
|---------|---------|-----|
| `403 Egress blocked: host not in allowlist` | A domain a sandbox script hits directly is not allowlisted | Allowlist that domain in the network policy (4.1) |
| `403 forbidden: unregistered skill proxy path` | The request reached the injection proxy, but the `proxy_path` is not registered | Confirm adapter.json exists and re-upload once (triggers registration); if it is still missing after a backend restart, check the egress side |
| `404` (wrong upstream path) | `endpoint_suffix` / `upstream_base` in adapter.json do not match the real API | Cross-check the third-party docs, fix the adapter, and re-upload |
| Answer only after multiple tool-call rounds | The first tool call failed and the LLM fell back | Look at the first round's error — usually one of the three cases above; it should succeed on the first round once fixed |
| Tools keep failing / timing out | Network unreachable or KEY invalid | Check the allowlist and whether the KEY in Settings is configured correctly |

---

## 6. Notes & Boundaries

1. **Never modify third-party skill sources**: all adaptation (endpoint redirection, KEY handling, command guidance) lives inside the `.ragclaw/` adapter; the third-party `SKILL.md` / `scripts/` stay as-is.
2. **Changes to egress code require rebuilding the image**: in dev mode backend / mcp-repl are bind-mounted (hot reload), but **egress is baked into the image** (`COPY` into the image), so after changing injection-proxy / egress related source you **must rebuild the `ragclaw-egress` image** for it to take effect.
3. **Data & secret locations**: skill files live on the shared volume (`/ragclaw_skills`); API KEYs live in `config.enc` (encrypted). Removing containers keeps the shared volume; wiping it re-seeds the factory skills.
4. **The main flow never special-cases a skill**: the system does not hard-code per-skill branches; every skill's capability and usage are defined by its own `SKILL.md` (+ `.ragclaw` notes).

---

## 7. FAQ

**Q: Is a skill usable right after upload? Does a container restart ever need to happen?**
A: Generally no restart is needed. Upload completes disk write, symlink, init, and upstream registration in one go; a new conversation is enough. Restart/rebuild is only needed when source code changes.

**Q: Why can't I see a freshly uploaded skill in my list?**
A: Check that it is "enabled" (enable symlink); confirm `SKILL.md` is at the package root and the frontmatter `name` is valid.

**Q: Where exactly does the API KEY live?**
A: Only in Settings (the single entry point). The KEY is stored encrypted in `config.enc` and at runtime only exists in the injection proxy's process memory — never visible inside the sandbox.

**Q: How do I confirm my requests go through the injection proxy instead of direct connections?**
A: A skill with `adapter.json` necessarily goes through the injection proxy; check `docker logs ragclaw-egress` for forwarding records for that folder.

**Q: Why write an adapter for a KEY-less skill (like anysearch)?**
A: The adapter lets the LLM locate the right command in one step (`SKILL.ragclaw.md` / `runtime.conf`) and routes everything through the injection proxy uniformly; configuring a KEY later requires no script changes.

**Q: What happens if I delete a factory-preset skill?**
A: The shared volume entry is removed; on the next backend restart it is re-seeded from `seeds/`.
