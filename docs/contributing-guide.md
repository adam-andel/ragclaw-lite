# Contributing Guide

> Thank you for helping to improve **RAGClaw**. This page explains how to report issues, submit changes, and review code. A Chinese version is available in [贡献指南.md](贡献指南.md).

---

## 1. Code of Conduct

Be respectful and collaborative. Maintainers may reject or revert contributions that involve harassment, abuse, or discrimination.

---

## 2. Principles in brief

- Keep changes small and focused; one PR solves one problem.
- Discuss before you build. For larger changes (new features, architectural changes, breaking changes), **open an issue first** and reach agreement before implementing.
- Only open PRs against the `main` branch.

---

## 3. Commit message conventions

Commit messages **must be in English** and follow `type(scope): subject`. `scope` is optional; include it when it adds clarity (e.g. `chat`, `egress`, `schema`, `docs`).

| type | Purpose |
|------|---------|
| `feat` | new feature |
| `fix` | bug fix |
| `refactor` | a refactor with no behavior change |
| `docs` | documentation |
| `test` | add or fix tests |
| `chore` | chores like dependencies or build |
| `perf` | performance improvement |

Examples:

```
fix(chat): handle zero-quota (unlimited) correctly on continue
refactor(egress): extract egress broker into independent ./egress directory
docs: sync secrets contract docs with current implementation
```

Do **not** stage files unrelated to the PR; never commit `.env`, secrets, or other sensitive material.

---

## 4. Code style

- Comments in code **must be in English**;
- `backend/` (Python): follow the existing style; add new dependencies to `requirements.txt` / `requirements-dev.txt` accordingly;
- `frontend/` (Vue): follow the existing `package.json` scripts and conventions;
- **Script parity**: `bin/psl` (Windows) and `bin/sh` (macOS/Linux) must stay aligned in config and behavior. When changing either, review the other set. psl is intentionally slimmed down and has no dev mode.

---

## 5. Testing

Changes must pass the existing suite; for logic changes, **add or update** the corresponding cases under `backend/tests/` (`unit/`, `api/`, `security/`, `integration/`).

This project's tests **run in container mode only** (local Python execution is not supported). Run them with:

```
# macOS / Linux
bash bin/sh/run_all_tests.sh

# Windows (CMD / PowerShell)
bin\psl\run_all_tests.ps1
```

Pay special attention to `security/` cases (auth, RBAC, conversation isolation, injection, IDOR, etc.). If your change touches that logic, add tests and note it in the PR.

---

## 6. Security-sensitive areas

The following areas sit on the security boundary and **must have their security impact described in the PR**:

- the execution sandbox (`mcp-repl` container, `read_only` + `seccomp` + `cap_drop`);
- the outbound network policy (`egress/`, injection proxy, allowlist);
- API-key management & injection (SKILL injection proxy, key env vars);
- SKILL upload / adapters (`.ragclaw/`, `shim.py`);
- user isolation (UID), RBAC / authorization, conversation isolation.

---

## 7. Pull Request workflow

1. Fork the repo and branch off `main` with a descriptive name;
2. Commit messages follow §3; keep the branch mergeable and the history semantically tidy;
3. Open the PR to `main`; describe what changed, why, how it was verified, and whether it touches the §6 security areas;
4. After CI / tests pass, maintainers review; revise per feedback without chaotic history (avoid gratuitous destructive rebases).

---

## 8. License

This project is licensed under the **Apache License 2.0** (see `LICENSE`). By contributing you agree to contribute under the same license; when importing substantial third-party code, note its provenance and license (e.g. for license-compatibility purposes).