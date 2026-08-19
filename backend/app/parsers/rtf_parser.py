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
"""RTF document parser using striprtf."""

from pathlib import Path

from striprtf.striprtf import rtf_to_text

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class RtfParser(BaseParser):
    """Parse .rtf files by stripping RTF control words and extracting plain text."""

    def extensions(self) -> list[str]:
        return ["rtf"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="rtf",
            display_name="parser.rtf.name",
            description="parser.rtf.desc",
            category="office",
            extensions=["rtf"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        raw = file_path.read_text(encoding="utf-8", errors="replace")
        text = rtf_to_text(raw)

        title = file_path.stem
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        sections: list[ParsedSection] = []
        for i, para in enumerate(paragraphs):
            first_line = para.split("\n", 1)[0]
            # Heuristic: short first line suggests a heading
            if len(first_line) < 80 and "\n" in para:
                sections.append(ParsedSection(level=1, heading=first_line, content=para))
            else:
                sections.append(ParsedSection(level=0, heading=title, content=para))

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=title, content=text.strip(),
            ))

        return ParsedDocument(
            title=title, file_type="rtf", sections=sections,
            metadata={"chars": len(text)},
        )
