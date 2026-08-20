#!/usr/bin/env bash
# Add Apache 2.0 license headers to Ragclaw source files.
# Idempotent: skips files that already have a header.
# Usage: cd /path/to/ragclaw && bash bin/sh/add-license-headers.sh

set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$PROJECT_ROOT"

HASH_HEADER='# Copyright 2026 徐松夏（Xu Songxia）
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.'

SLASH_HEADER='// Copyright 2026 徐松夏（Xu Songxia）
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//     http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.'

VUE_HEADER='<!--
  Copyright 2026 徐松夏（Xu Songxia）

  Licensed under the Apache License, Version 2.0 (the "License");
  you may not use this file except in compliance with the License.
  You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

  Unless required by applicable law or agreed to in writing, software
  distributed under the License is distributed on an "AS IS" BASIS,
  WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
  See the License for the specific language governing permissions and
  limitations under the License.
-->'

added=0
skipped=0

has_header() {
    head -30 "$1" | grep -q "Apache License, Version 2.0" 2>/dev/null
}

# Insert $2 (header text) into file $1.
# If the file starts with a shebang, keep the shebang on line 1 and put the
# header on line 2; otherwise prepend the header at the top.
add_header() {
    local file="$1" header="$2"
    if has_header "$file"; then
        skipped=$((skipped + 1))
        return 0
    fi
    echo "  + $file"
    if head -1 "$file" | grep -q '^#!/'; then
        { head -1 "$file"; echo "$header"; tail -n +2 "$file"; } > "${file}.tmp" && mv "${file}.tmp" "$file"
    else
        { echo "$header"; cat "$file"; } > "${file}.tmp" && mv "${file}.tmp" "$file"
    fi
    added=$((added + 1))
}

# ---- Python / Shell / Dockerfile share the '#' comment style ----
add_hash_header() {
    add_header "$1" "$HASH_HEADER"
}

add_vue_header() {
    add_header "$1" "$VUE_HEADER"
}

echo "=== Python (.py) ==="
while IFS= read -r -d '' f; do
    add_hash_header "$f"
done < <(
    find backend/app backend/tests mcp egress \
         backend/seeds/skills/anysearch/scripts \
         backend/seeds/skills/anysearch/.ragclaw \
         -name '*.py' -type f -print0 2>/dev/null
)

echo "=== TypeScript (.ts) ==="
while IFS= read -r -d '' f; do
    add_header "$f" "$SLASH_HEADER"
done < <(find frontend/src -name '*.ts' -type f -print0 2>/dev/null)

echo "=== Vue (.vue) ==="
while IFS= read -r -d '' f; do
    add_vue_header "$f"
done < <(find frontend/src -name '*.vue' -type f -print0 2>/dev/null)

echo "=== Shell (.sh) ==="
while IFS= read -r -d '' f; do
    add_hash_header "$f"
done < <(find bin/sh nginx -name '*.sh' -type f -print0 2>/dev/null)

echo "=== Dockerfiles ==="
for f in \
    Dockerfile \
    backend/Dockerfile.dev \
    frontend/Dockerfile.dev \
    mcp/Dockerfile \
    mcp/Dockerfile.dev \
    nginx/Dockerfile \
    egress/Dockerfile.egress
do
    [ -f "$f" ] || { echo "  (not found: $f)"; continue; }
    add_hash_header "$f"
done

echo ""
echo "Done.  Added: $added  Skipped (already has header): $skipped"