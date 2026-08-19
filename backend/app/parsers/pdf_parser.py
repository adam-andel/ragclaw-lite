"""PDF document parser using pdfplumber."""

import re
from pathlib import Path
import pdfplumber

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class PDFParser(BaseParser):
    """Parse PDF files, extracting text + structure (headings, pages)."""

    def extensions(self) -> list[str]:
        return ["pdf"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="pdf",
            display_name="parser.pdf.name",
            description="parser.pdf.desc",
            category="office",
            extensions=["pdf"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        doc = pdfplumber.open(file_path)
        title = file_path.stem
        all_sections: list[ParsedSection] = []

        for page_num, page in enumerate(doc.pages):
            text = page.extract_text()

            if not text or not text.strip():
                continue

            words = page.extract_words(extra_attrs=["size"])
            if not words:
                continue

            lines = self._words_to_lines(words)
            lines.sort(key=lambda l: (l["top"], l["x0"]))

            for line in lines:
                line_text = line["text"].strip()
                if not line_text:
                    continue

                font_size = line["size"]

                if self._is_heading(line_text, font_size):
                    level = self._guess_heading_level(line_text, font_size)
                    all_sections.append(ParsedSection(
                        level=level,
                        heading=line_text,
                        content=line_text,
                        page=page_num + 1,
                    ))
                else:
                    if all_sections:
                        all_sections[-1].content += "\n" + line_text
                    else:
                        all_sections.append(ParsedSection(
                            level=0,
                            heading=title,
                            content=line_text,
                            page=page_num + 1,
                        ))

        doc.close()

        if not title:
            title = file_path.stem

        return ParsedDocument(
            title=title,
            file_type="pdf",
            sections=all_sections,
            metadata={
                "page_count": len(doc.pages),
            },
        )

    def _words_to_lines(self, words: list[dict]) -> list[dict]:
        """Group words into lines by y-position proximity."""
        if not words:
            return []

        sorted_words = sorted(words, key=lambda w: (w["top"], w["x0"]))
        lines = []
        current_line = {
            "text": sorted_words[0]["text"],
            "top": sorted_words[0]["top"],
            "x0": sorted_words[0]["x0"],
            "size": sorted_words[0].get("size", 0),
            "word_count": 1,
        }

        for word in sorted_words[1:]:
            if abs(word["top"] - current_line["top"]) <= 5:
                current_line["text"] += " " + word["text"]
                current_line["size"] = max(current_line["size"], word.get("size", 0))
                current_line["word_count"] += 1
            else:
                lines.append(current_line)
                current_line = {
                    "text": word["text"],
                    "top": word["top"],
                    "x0": word["x0"],
                    "size": word.get("size", 0),
                    "word_count": 1,
                }

        lines.append(current_line)
        return lines

    def _is_heading(self, text: str, font_size: float) -> bool:
        """Heuristic: short text with larger font is likely a heading."""
        text = text.strip()
        if len(text) > 120:
            return False
        if font_size > 12:
            return True
        heading_patterns = [
            r'^第[一二三四五六七八九十\d]+[章节]',
            r'^\d+(\.\d+)*\s+',
            r'^[一二三四五六七八九十]+[、．.]',
        ]
        for pat in heading_patterns:
            if re.match(pat, text):
                return True
        return False

    def _guess_heading_level(self, text: str, font_size: float) -> int:
        """Guess heading level based on font size and text pattern."""
        if re.match(r'^第[一二三四五六七八九十\d]+章', text):
            return 1
        if re.match(r'^第[一二三四五六七八九十\d]+节', text):
            return 2
        if re.match(r'^\d+\.\d+\.\d+', text):
            return 3
        if re.match(r'^\d+\.\d+', text):
            return 2
        if re.match(r'^\d+[、.．]', text):
            return 1
        if font_size > 16:
            return 1
        if font_size > 13:
            return 2
        if font_size > 11:
            return 3
        return 2
