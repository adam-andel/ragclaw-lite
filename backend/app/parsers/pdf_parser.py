"""PDF document parser using PyMuPDF."""

import re
from pathlib import Path
import pymupdf

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class PDFParser(BaseParser):
    """Parse PDF files, extracting text + structure (headings, pages)."""

    def extensions(self) -> list[str]:
        return ["pdf"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="pdf",
            display_name="PDF 文档",
            description="解析 PDF 文件，按页提取文本，依据字体大小检测标题层级",
            category="office",
            extensions=["pdf"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        doc = pymupdf.open(file_path)
        title = file_path.stem
        all_sections: list[ParsedSection] = []

        for page_num in range(len(doc)):
            page = doc[page_num]
            text = page.get_text("text")

            if not text.strip():
                continue

            # Extract blocks for better structure detection
            blocks = page.get_text("blocks")
            blocks.sort(key=lambda b: (b[1], b[0]))  # sort by y, then x

            for block in blocks:
                block_text = block[4].strip()
                if not block_text:
                    continue

                # Detect if this looks like a heading (font size heuristic)
                # In PyMuPDF, block[3]-block[1] is height, can approximate font size
                font_size_approx = block[3] - block[1] if len(block) >= 4 else 0

                if self._is_heading(block_text, font_size_approx):
                    level = self._guess_heading_level(block_text, font_size_approx)
                    all_sections.append(ParsedSection(
                        level=level,
                        heading=block_text,
                        content=block_text,
                        page=page_num + 1,
                    ))
                else:
                    # Append to last section or create a new one
                    if all_sections:
                        all_sections[-1].content += "\n" + block_text
                    else:
                        all_sections.append(ParsedSection(
                            level=0,
                            heading=title,
                            content=block_text,
                            page=page_num + 1,
                        ))

        doc.close()

        # Use filename as title if no obvious title found
        if not title:
            title = file_path.stem

        return ParsedDocument(
            title=title,
            file_type="pdf",
            sections=all_sections,
            metadata={
                "page_count": len(doc) if hasattr(doc, '__len__') else 0,
            },
        )

    def _is_heading(self, text: str, font_size: float) -> bool:
        """Heuristic: short text with larger font is likely a heading."""
        text = text.strip()
        if len(text) > 120:
            return False
        if font_size > 12:
            return True
       # Match patterns like "Chapter X", "1. ", "1.1 ", etc..
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
