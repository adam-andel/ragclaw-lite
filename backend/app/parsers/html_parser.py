"""HTML document parser using BeautifulSoup + lxml."""

from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class HtmlParser(BaseParser):
    """Parse HTML files, splitting by <h1>..<h6> headings.

    Strips <script>, <style>, and other non-content tags. The <title> tag
    becomes the document title. Headings define section boundaries; body
    content between headings is appended to the most recent section.
    """

    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}

    def extensions(self) -> list[str]:
        return ["html", "htm"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="html",
            display_name="parser.html.name",
            description="parser.html.desc",
            category="web",
            extensions=["html", "htm"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        html = file_path.read_text(encoding="utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        # Strip noise tags
        for tag in soup(["script", "style", "noscript", "iframe", "svg"]):
            tag.decompose()

        title = (soup.title.string.strip() if soup.title and soup.title.string
                 else file_path.stem)

        sections: list[ParsedSection] = []
        current: ParsedSection | None = None

        # Walk the body in document order so heading/text interleaving is preserved.
        body = soup.body or soup
        for el in body.descendants:
            if not isinstance(el, Tag):
                continue
            if el.name in self._HEADING_TAGS:
                heading_text = el.get_text(strip=True)
                if not heading_text:
                    continue
                level = int(el.name[1])
                current = ParsedSection(
                    level=level,
                    heading=heading_text,
                    content=heading_text,
                )
                sections.append(current)
            elif current is not None and el.name == "p":
                p_text = el.get_text(separator=" ", strip=True)
                if p_text:
                    current.content += "\n" + p_text
            elif current is not None and el.name in ("li",):
                li_text = el.get_text(separator=" ", strip=True)
                if li_text:
                    current.content += "\n- " + li_text

        if not sections:
            full_text = body.get_text(separator="\n", strip=True)
            if full_text:
                sections.append(ParsedSection(
                    level=0, heading=title, content=full_text,
                ))

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=title, content="",
            ))

        return ParsedDocument(
            title=title, file_type="html", sections=sections,
            metadata={"title_tag": title},
        )
