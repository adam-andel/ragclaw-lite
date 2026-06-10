"""Markdown document parser."""

import re
from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection


class MarkdownParser(BaseParser):
    """Parse Markdown files, using heading markers for structure."""

    HEADING_RE = re.compile(r'^(#{1,6})\s+(.+)$', re.MULTILINE)

    def can_handle(self, file_type: str) -> bool:
        return file_type.lower() in ("md", "markdown")

    def parse(self, file_path: Path) -> ParsedDocument:
        text = file_path.read_text(encoding="utf-8")

        # Extract title from first H1 or filename
        title = file_path.stem
        first_h1 = re.search(r'^#\s+(.+)$', text, re.MULTILINE)
        if first_h1:
            title = first_h1.group(1).strip()

        # Remove YAML front matter
        text = self._strip_front_matter(text)

        # Clean up markdown syntax for better text quality
        text = self._clean_markdown(text)

        sections: list[ParsedSection] = []
        current_section: ParsedSection | None = None

        for line in text.split("\n"):
            line_stripped = line.strip()
            if not line_stripped:
                if current_section:
                    current_section.content += "\n"
                continue

            m = self.HEADING_RE.match(line)
            if m:
                level = len(m.group(1))
                heading = m.group(2).strip()
                section = ParsedSection(
                    level=min(level, 6),
                    heading=heading,
                    content=heading,
                )
                sections.append(section)
                current_section = section
                if level == 1 and title == file_path.stem:
                    title = heading
            elif current_section:
                current_section.content += "\n" + line_stripped
            else:
                sections.append(ParsedSection(
                    level=0,
                    heading=title,
                    content=line_stripped,
                ))
                current_section = sections[-1]

        return ParsedDocument(
            title=title,
            file_type="markdown",
            sections=sections,
            metadata={"chars": len(text)},
        )

    def _strip_front_matter(self, text: str) -> str:
        """Remove YAML front matter (between --- markers)."""
        if text.startswith("---"):
            end = text.find("---", 3)
            if end != -1:
                return text[end + 3:].strip()
        return text

    def _clean_markdown(self, text: str) -> str:
        """Strip markdown formatting tokens for cleaner text."""
        # Remove image links
        text = re.sub(r'!\[.*?\]\(.*?\)', '', text)
        # Keep link text, remove URL
        text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
        # Remove bold/italic markers but keep text
        text = re.sub(r'\*\*\*(.+?)\*\*\*', r'\1', text)
        text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
        text = re.sub(r'\*(.+?)\*', r'\1', text)
        text = re.sub(r'__(.+?)__', r'\1', text)
        text = re.sub(r'_(.+?)_', r'\1', text)
        # Remove inline code markers
        text = re.sub(r'`([^`]+)`', r'\1', text)
        # Remove horizontal rules
        text = re.sub(r'^[-*_]{3,}\s*$', '', text, flags=re.MULTILINE)
        return text
