#!/usr/bin/env python3
"""secret-zero shim — ragclaw-owned adapter, NOT third-party source.

Generic adapter that redirects a third-party skill CLI's outbound API call
through the ragclaw injection proxy, so the API KEY never touches the REPL
sandbox (FS / env / process memory). Per-skill differences are described by
``adapter.json`` located next to this file. The third-party CLI is imported
and patched in-memory; its source is never modified.

Usage (invoked by the LLM inside the REPL sandbox):
    python3 $REPL_SKILLS_DIR/<skill>/.ragclaw/shim.py <same args as the CLI>
"""
import importlib
import json
import os
import sys

# The sandbox reaches the egress broker via REPL_EGRESS_HOST (the internal-network
# IP, e.g. 172.30.0.2) — its own DNS points there and does NOT resolve Docker
# service names like "ragclaw-egress". So prefer that env var when present, and
# only fall back to the service name in non-sandbox / dev contexts.
PROXY_HOST = os.environ.get("REPL_EGRESS_HOST") or os.environ.get(
    "RAGCLAW_EGRESS_HOST", "ragclaw-egress"
)
PROXY_PORT = os.environ.get("RAGCLAW_EGRESS_SECRET_PORT", "9090")


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))

    # 1) Load the per-skill descriptor written by the seed / backend.
    adapter_path = os.path.join(here, "adapter.json")
    try:
        with open(adapter_path, encoding="utf-8") as f:
            ad = json.load(f)
    except (OSError, ValueError) as e:
        print(f"[ragclaw-shim] cannot read adapter.json: {e}", file=sys.stderr)
        return 2

    cli_module = ad["cli_module"]
    endpoint_attr = ad["endpoint_attr"]
    key_env = ad.get("key_env")
    proxy_path = ad["proxy_path"]
    endpoint_suffix = ad.get("endpoint_suffix", "")

    # 2) Make the third-party CLI importable (it lives in ../scripts).
    scripts_dir = os.path.join(here, "..", "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    # 3) Import the CLI module. Its top-level _load_env() may read a local .env
    #    into the process env — strip the KEY env immediately so the sandbox
    #    never carries the real credential into the outbound call.
    try:
        cli = importlib.import_module(cli_module)
    except Exception as e:  # noqa: BLE001 - surface any import failure cleanly
        print(f"[ragclaw-shim] cannot import {cli_module}: {e}", file=sys.stderr)
        return 2

    if key_env:
        os.environ.pop(key_env, None)

    # 4) Redirect the CLI's outbound endpoint to the injection proxy. This is an
    #    in-memory patch only; the third-party source file is untouched.
    proxy_url = f"http://{PROXY_HOST}:{PROXY_PORT}/{proxy_path}{endpoint_suffix}"
    setattr(cli, endpoint_attr, proxy_url)

    # 5) Run the CLI's own entry point unchanged; the proxy injects the KEY.
    sys.argv[0] = f"{cli_module}.py"
    try:
        cli.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
