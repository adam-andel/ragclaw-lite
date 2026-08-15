"""Document parser dispatcher - routes to format-specific parsers.

Auto-discovers parser classes in app.parsers package via pkgutil.
Adding a new format = dropping a new <name>_parser.py file in the package.
No need to edit this file or maintain a hardcoded list.
"""

import importlib
import pkgutil
import time
from pathlib import Path

from sqlalchemy import select

import app.parsers as parsers_pkg
from app.parsers.base import BaseParser, ParsedDocument


class ParserService:
    """Central dispatcher that picks the right parser for a file."""

    _CACHE_TTL_SEC = 60

    def __init__(self):
        self._parsers: list[BaseParser] = []
        self._disabled_names: set[str] = set()
        self._cache_ts: float = 0.0
        self._discover_internal()

    def _discover_internal(self) -> None:
        """Walk app.parsers package and instantiate every BaseParser subclass.

        Filters out abstract classes and re-exports (attr.__module__ check
        ensures we only pick up classes actually defined in the module being
        scanned, not imports).
        """
        self._parsers = []
        for _, module_name, _ in pkgutil.iter_modules(parsers_pkg.__path__):
            module = importlib.import_module(f"app.parsers.{module_name}")
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (isinstance(attr, type)
                        and issubclass(attr, BaseParser)
                        and attr is not BaseParser
                        and attr.__module__ == module.__name__):
                    self._parsers.append(attr())

    def parse(self, file_path: Path, file_type: str) -> ParsedDocument:
        """Parse a document using the appropriate parser.

        Args:
            file_path: Absolute path to the document file
            file_type: File extension without dot (pdf, docx, md, txt)

        Returns:
            ParsedDocument with structured sections

        Raises:
            ValueError: If no parser can handle the file type (or the parser
                        is disabled, or parsing fails inside safe_parse()).
        """
        file_type = file_type.lower().lstrip(".")

        for parser in self._parsers:
            if parser.can_handle(file_type):
                if parser.plugin_meta().name in self._disabled_names:
                    raise ValueError(
                        f"PLUGIN_DISABLED: {parser.plugin_meta().name}"
                    )
                return parser.safe_parse(file_path)

        raise ValueError(f"No parser available for file type: {file_type}")

    def supported_types(self) -> list[str]:
        """Auto-aggregated from all *enabled* parsers' extensions()."""
        seen, types = set(), []
        for p in self._parsers:
            if p.plugin_meta().name in self._disabled_names:
                continue
            for ext in p.extensions():
                if ext not in seen:
                    seen.add(ext)
                    types.append(ext)
        return types

    def list_plugins(self) -> list[dict]:
        """Return metadata for every parser (used by /api/plugins endpoint)."""
        return [
            {
                "name": p.plugin_meta().name,
                "display_name": p.plugin_meta().display_name,
                "description": p.plugin_meta().description,
                "category": p.plugin_meta().category,
                "extensions": p.extensions(),
                "version": p.plugin_meta().version,
            }
            for p in self._parsers
        ]

    async def _refresh_disabled_cache(self) -> None:
        """Load disabled plugin names from DB into memory.

        Called on startup + after admin enable/disable mutation.
        Uses lazy imports to avoid circular dependency at module load time.
        """
        from app.database import async_session
        from app.models.parser_plugin import ParserPluginState

        async with async_session() as db:
            result = await db.execute(
                select(ParserPluginState.name).where(
                    ParserPluginState.disabled == True  # noqa: E712
                )
            )
            self._disabled_names = {row[0] for row in result.all()}
        self._cache_ts = time.time()

    async def _ensure_cache_fresh(self) -> None:
        """Lazy refresh if TTL expired. Safe to call from async contexts."""
        if time.time() - self._cache_ts > self._CACHE_TTL_SEC:
            await self._refresh_disabled_cache()


# Singleton
parser_service = ParserService()
