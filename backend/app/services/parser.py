"""Document parser dispatcher - routes to format-specific parsers."""

from pathlib import Path

from app.parsers.base import BaseParser, ParsedDocument
from app.parsers.pdf_parser import PDFParser
from app.parsers.word_parser import WordParser
from app.parsers.markdown_parser import MarkdownParser
from app.parsers.txt_parser import TxtParser


class ParserService:
    """Central dispatcher that picks the right parser for a file."""

    def __init__(self):
        self._parsers: list[BaseParser] = [
            PDFParser(),
            WordParser(),
            MarkdownParser(),
            TxtParser(),
        ]

    def parse(self, file_path: Path, file_type: str) -> ParsedDocument:
        """Parse a document using the appropriate parser.

        Args:
            file_path: Absolute path to the document file
            file_type: File extension without dot (pdf, docx, md, txt)

        Returns:
            ParsedDocument with structured sections

        Raises:
            ValueError: If no parser can handle the file type
        """
        file_type = file_type.lower().lstrip(".")

        for parser in self._parsers:
            if parser.can_handle(file_type):
                return parser.parse(file_path)

        raise ValueError(f"No parser available for file type: {file_type}")

    def supported_types(self) -> list[str]:
        return ["pdf", "docx", "doc", "md", "markdown", "txt"]


# Singleton
parser_service = ParserService()
