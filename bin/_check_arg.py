import re
files = ["Dockerfile", "backend/Dockerfile.dev", "mcp/Dockerfile", "mcp/Dockerfile.dev"]
pat = re.compile(r"ARG (REGISTRY|APT_MIRROR|PYPI_MIRROR|TORCH_INDEX)")
for f in files:
    print("=== %s ===" % f)
    with open(f, encoding="utf-8") as fh:
        for i, l in enumerate(fh, 1):
            if pat.search(l) or "single source of truth" in l:
                print("%d: %s" % (i, l.rstrip()))
