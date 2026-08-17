# .ragclaw/ — ragclaw system-owned files for this skill

This directory holds all ragclaw framework files for the skill. It is **NOT**
part of the third-party skill source and must never be edited by the skill
author or shipped inside the upstream zip — the backend manages it exclusively.

Files:

- `init.sh`      — unified per-skill init hook, run on enable / re-upload
                   (materialises `runtime.conf` from `runtime.conf.example`). Never
                   mutates the third-party `SKILL.md`.
- `shim.py`      — (secret-zero) sandbox trigger that redirects the third-party
                   CLI to the injected proxy. Always present; the proxy forwards
                   anonymously when no KEY is configured.
- `adapter.json` — (secret-zero) descriptor declaring the skill's endpoint / KEY
                   env var / upstream base. Always present.
- `SKILL.ragclaw.md` — committed statically (NOT generated at runtime);
                   ragclaw-owned LLM guidance (resolved command + output rules).
                   Surfaced as an extra context block by the loader; the native
                   SKILL.md is untouched.
- `README.md`    — this file.

The native skill files (`SKILL.md`, `scripts/`, `runtime.conf.example`, ...) live
at the package root, flat and untouched.

Re-upload rule: the backend stashes this whole directory before replacing the
skill, then restores it, so local ragclaw state survives a native upgrade.
