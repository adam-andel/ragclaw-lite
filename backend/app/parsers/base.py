"""Abstract base class for document parsers."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ParsedSection:
    """A parsed section from a document."""
    level: int            # heading level (0 = root/title, 1 = H1, 2 = H2, ...)
    heading: str          # section heading text
    content: str          # full text content of this section
    page: int | None = None  # page number (PDF only)


@dataclass
class ParsedDocument:
    """The unified output of any document parser."""
    title: str
    file_type: str
    sections: list[ParsedSection] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n\n".join(s.content for s in self.sections)

    @property
    def section_count(self) -> int:
        return len(self.sections)


class BaseParser(ABC):
    """All parsers must implement this interface."""

    @abstractmethod
    def can_handle(self, file_type: str) -> bool:
        """Check if this parser can handle the given file type."""
        ...

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse a document file and return structured output."""
        ...
