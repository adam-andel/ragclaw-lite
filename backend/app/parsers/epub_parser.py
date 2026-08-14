"""EPUB ebook parser using ebooklib."""

from pathlib import Path

from ebooklib import epub

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class EpubParser(BaseParser):
    """Parse .epub files, producing one section per chapter (spine item)."""

    def extensions(self) -> list[str]:
        return ["epub"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="epub",
            display_name="parser.epub.name",
            description="parser.epub.desc",
            category="ebook",
            extensions=["epub"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        book = epub.read_epub(file_path)

        title = file_path.stem
        if book.get_metadata("DC", "title"):
            title = book.get_metadata("DC", "title")[0][0] or title

        sections: list[ParsedSection] = []
        chapter_idx = 0

        for item in book.get_items_of_type(9):  # ITEM_DOCUMENT = 9
            chapter_idx += 1
            html_content = item.get_content().decode("utf-8", errors="replace")
            if not html_content.strip():
                continue

            heading, body_text = self._extract_html(html_content)
            if not heading:
                heading = f"Chapter {chapter_idx}"
            if not body_text.strip():
                continue

            sections.append(ParsedSection(
                level=1, heading=heading, content=body_text, page=chapter_idx,
            ))

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=title, content="",
            ))

        return ParsedDocument(
            title=title, file_type="epub", sections=sections,
            metadata={"chapter_count": chapter_idx},
        )

    def _extract_html(self, html: str) -> tuple[str, str]:
        """Minimal HTML extractor: pull title from <h1>/<title>, body from <p> tags."""
        import re
        # Extract heading
        heading = ""
        for pat in (r"<h1[^>]*>(.*?)</h1>", r"<title>(.*?)</title>"):
            m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if m:
                heading = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                break
        # Extract paragraphs
        paragraphs = re.findall(r"<p[^>]*>(.*?)</p>", html, re.IGNORECASE | re.DOTALL)
        body_parts = []
        for p in paragraphs:
            clean = re.sub(r"<[^>]+>", "", p).strip()
            if clean:
                body_parts.append(clean)
        return heading, "\n".join(body_parts)
