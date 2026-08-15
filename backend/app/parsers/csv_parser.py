"""CSV document parser using the stdlib csv module."""

import csv
from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class CsvParser(BaseParser):
    """Parse CSV files, grouping every N rows into a single section.

    CSV is row-oriented, so we batch rows to keep sections at a reasonable
    size for downstream chunking. The first row is treated as a header if it
    looks like one (heuristic: all cells non-numeric and short).
    """

    _ROWS_PER_SECTION = 50

    def extensions(self) -> list[str]:
        return ["csv"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="csv",
            display_name="parser.csv.name",
            description="parser.csv.desc",
            category="data",
            extensions=["csv"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        # Try utf-8 first; fall back to gbk for legacy Chinese exports.
        text = self._read_text(file_path)

        rows = list(csv.reader(text.splitlines()))
        if not rows:
            return ParsedDocument(
                title=file_path.stem, file_type="csv", sections=[],
                metadata={"row_count": 0},
            )

        header = rows[0] if self._looks_like_header(rows[0]) else None
        data_rows = rows[1:] if header else rows

        sections: list[ParsedSection] = []
        for i in range(0, len(data_rows), self._ROWS_PER_SECTION):
            batch = data_rows[i:i + self._ROWS_PER_SECTION]
            if header:
                lines = ["\t".join(header)] + ["\t".join(r) for r in batch]
            else:
                lines = ["\t".join(r) for r in batch]
            sections.append(ParsedSection(
                level=1,
                heading=f"{file_path.stem} - Section {i // self._ROWS_PER_SECTION + 1}",
                content="\n".join(lines),
            ))

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=file_path.stem, content="",
            ))

        return ParsedDocument(
            title=file_path.stem, file_type="csv", sections=sections,
            metadata={
                "row_count": len(data_rows),
                "column_count": len(header) if header else 0,
                "has_header": header is not None,
            },
        )

    def _read_text(self, file_path: Path) -> str:
        for encoding in ("utf-8-sig", "utf-8", "gbk", "gb2312", "latin-1"):
            try:
                return file_path.read_text(encoding=encoding)
            except UnicodeDecodeError:
                continue
        # Last resort
        return file_path.read_text(encoding="latin-1", errors="replace")

    def _looks_like_header(self, row: list[str]) -> bool:
        if not row:
            return False
        # Header cells tend to be non-empty, short, and non-numeric.
        non_empty = [c for c in row if c.strip()]
        if len(non_empty) < len(row) / 2:
            return False
        return all(len(c.strip()) < 64 for c in row)
