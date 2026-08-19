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
"""Plain text document parser."""

from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class TxtParser(BaseParser):
    """Parse plain text files by splitting on empty lines as paragraph boundaries."""

    def extensions(self) -> list[str]:
        return ["txt"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="txt",
            display_name="parser.txt.name",
            description="parser.txt.desc",
            category="text",
            extensions=["txt"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        # Try multiple encodings
        text = ""
        for encoding in ("utf-8", "gbk", "gb2312", "latin-1"):
            try:
                text = file_path.read_text(encoding=encoding)
                break
            except (UnicodeDecodeError, UnicodeError):
                continue

        title = file_path.stem

        # Split by blank lines for paragraph grouping
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]

        if not paragraphs:
            return ParsedDocument(title=title, file_type="txt", sections=[])

        sections: list[ParsedSection] = []

        for i, para in enumerate(paragraphs):
            lines = para.split("\n")
            first_line = lines[0]

            # Heuristic: if first line is short, treat it as a heading
            if len(first_line) < 80 and len(lines) > 1:
                sections.append(ParsedSection(
                    level=1,
                    heading=first_line,
                    content=para,
                ))
            else:
                sections.append(ParsedSection(
                    level=0,
                    heading=title,
                    content=para,
                ))

        # If no sections detected, treat entire text as one section
        if not sections:
            sections.append(ParsedSection(
                level=0,
                heading=title,
                content=text.strip(),
            ))

        return ParsedDocument(
            title=title,
            file_type="txt",
            sections=sections,
            metadata={"chars": len(text)},
        )
