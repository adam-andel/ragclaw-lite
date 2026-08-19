# Copyright 2026 徐松夏（Xu Songxia）
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
# limitations under the License.
"""Skill exposure inside the REPL sandbox.

Historically this module materialised a *per-user* symlink
``<sandbox>/.ragclaw/skills/<name>`` inside each user's mcp-repl sandbox so the
LLM could read a skill's resources. That per-user symlink tree has been removed:
skills are now exposed to the sandbox exclusively through the ``REPL_SKILLS_DIR``
*persistent container environment variable* (defined in docker-compose for the
mcp-repl service, default ``/ragclaw_skills/enable``), which points at the shared,
backend-managed ``enable/`` set. No per-user filesystem machinery is needed.

``ensure_user_skill_link`` is kept as a no-op for backward compatibility with the
single call site in ``agent_nodes._load_skill_body_and_tools``; it now does
nothing and always reports success.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("ragclaw.skill_link")


async def ensure_user_skill_link(user_id: str, folder_name: str) -> bool:
    """No-op.

    Per-user sandbox symlinks were removed; the shared skill set is exposed via
    the ``REPL_SKILLS_DIR`` container env var instead. Returns True so callers
    that depend on a truthy result keep working unchanged.
    """
    return True
