"""JSON document parser using the stdlib json module."""

import json
from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class JsonParser(BaseParser):
    """Parse JSON files, walking the top-level structure into sections.

    For a top-level object, each key becomes a section. For a top-level
    array, every N items becomes a section. Nested structures are pretty-
    printed into the section content.
    """

    _ITEMS_PER_SECTION = 20

    def extensions(self) -> list[str]:
        return ["json"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="json",
            display_name="parser.json.name",
            description="parser.json.desc",
            category="data",
            extensions=["json"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        text = file_path.read_text(encoding="utf-8")
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON_PARSE_FAILED: {e}") from e

        sections: list[ParsedSection] = []

        if isinstance(data, dict):
            for key, value in data.items():
                content = json.dumps(value, ensure_ascii=False, indent=2)
                sections.append(ParsedSection(
                    level=1,
                    heading=str(key),
                    content=f"{key}: {content}",
                ))
        elif isinstance(data, list):
            for i in range(0, len(data), self._ITEMS_PER_SECTION):
                batch = data[i:i + self._ITEMS_PER_SECTION]
                content = json.dumps(batch, ensure_ascii=False, indent=2)
                sections.append(ParsedSection(
                    level=1,
                    heading=f"{file_path.stem} - Section {i // self._ITEMS_PER_SECTION + 1}",
                    content=content,
                ))
        else:
            # Scalar JSON (string/number/bool/null)
            sections.append(ParsedSection(
                level=0,
                heading=file_path.stem,
                content=json.dumps(data, ensure_ascii=False),
            ))

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=file_path.stem, content="",
            ))

        return ParsedDocument(
            title=file_path.stem, file_type="json", sections=sections,
            metadata={
                "type": type(data).__name__,
                "top_level_count": len(data) if isinstance(data, (list, dict)) else 1,
            },
        )
