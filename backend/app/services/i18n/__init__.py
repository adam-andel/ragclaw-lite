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
"""Backend i18n for agent-graph prompts — mirrors frontend/src/i18n.

Layout (like the frontend locale dirs):
  - zh_cn.py  -> MESSAGES dict (Chinese, original behavior)
  - en_us.py  -> MESSAGES dict (English A/B variants for English-dominant base models)
  - __init__  -> assembles both and exposes a single ``t()`` resolver

Prompt templates use named placeholders (``{query}``, ``{skill_list}``, ``{tool_desc}``,
``{name}``, ``{switch_count}``, ``{quota}``, ``{tool_name}``, ``{snippet}``). They are
rendered with a brace-safe substitution that leaves literal ``{``/``}`` (e.g. JSON
examples) untouched, so no double-brace escaping is needed in the source templates.

Backend stores the locale as a short code (``prompt_language`` = 'zh' | 'en');
``normalize_locale`` maps it to the canonical 'zh-CN' / 'en-US' keys used here.
"""

import re

from app.services.i18n.zh_cn import MESSAGES as _ZH
from app.services.i18n.en_us import MESSAGES as _EN

SUPPORTED_LOCALES = ("zh-CN", "en-US")
DEFAULT_LOCALE = "zh-CN"

_LOCALES = {
    "zh-CN": _ZH,
    "en-US": _EN,
}

# Matches a single {word} placeholder; literal braces with spaces/quotes (JSON
# examples like {"tool": ...} or empty `{}`) are intentionally NOT matched.
_PARAM_RE = re.compile(r"\{(\w+)\}")


def normalize_locale(locale: str | None) -> str:
    """Map backend 'zh'/'en' (and case variants) to a canonical 'zh-CN'/'en-US' key."""
    if not locale:
        return DEFAULT_LOCALE
    loc = str(locale).strip().lower()
    if loc in ("zh", "zh-cn", "chinese"):
        return "zh-CN"
    if loc in ("en", "en-us", "english"):
        return "en-US"
    if locale in SUPPORTED_LOCALES:
        return locale
    return DEFAULT_LOCALE


def _render(template: str, **kwargs) -> str:
    """Substitute {word} placeholders, leaving unmatched/non-word braces intact."""
    if not kwargs:
        return template

    def _sub(match: "re.Match") -> str:
        key = match.group(1)
        return str(kwargs[key]) if key in kwargs else match.group(0)

    return _PARAM_RE.sub(_sub, template)


def t(prompt_id: str, lang: str = DEFAULT_LOCALE, **kwargs) -> str:
    """Resolve a prompt template by id + locale and format it with kwargs.

    Falls back to the Chinese template if the requested locale or id is missing,
    preserving the original default behavior.
    """
    messages = _LOCALES.get(normalize_locale(lang), _ZH)
    template = messages.get(prompt_id, _ZH.get(prompt_id, ""))
    return _render(template, **kwargs)
