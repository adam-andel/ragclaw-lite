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


@dataclass
class ParserPluginMeta:
    """Static metadata describing a parser plugin, surfaced to the admin UI."""
    name: str                          # unique key, e.g. "excel"
    display_name: str                 # UI label, e.g. "Excel spreadsheet""
    description: str                   # one-line description
    category: str                      # office|data|web|email|ebook|text|notebook
    extensions: list[str]              # MUST match extensions() return value
    enabled_by_default: bool = True
    version: str = "1.0.0"


class BaseParser(ABC):
    """All parsers must implement this interface.

    Subclasses must implement extensions(), parse(), and plugin_meta().
    The default can_handle() delegates to extensions() so subclasses no
    longer need to override it.
    """

    @abstractmethod
    def extensions(self) -> list[str]:
        """Return supported extensions without dot, e.g. ['xlsx', 'xls']."""
        ...

    @abstractmethod
    def parse(self, file_path: Path) -> ParsedDocument:
        """Parse a document file and return structured output."""
        ...

    @classmethod
    @abstractmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        """Return static metadata for plugin management UI."""
        ...

    def can_handle(self, file_type: str) -> bool:
        """Default implementation: subclasses no longer override this."""
        return file_type.lower().lstrip(".") in self.extensions()

    def safe_parse(self, file_path: Path) -> ParsedDocument:
        """Wrap parse() so library exceptions become ValueError for the pipeline."""
        try:
            return self.parse(file_path)
        except Exception as e:
            raise ValueError(
                f"{self.__class__.__name__} PARSER_PARSE_FAILED: {e}"
            ) from e
