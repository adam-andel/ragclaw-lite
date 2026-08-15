"""Jupyter Notebook (.ipynb) parser using nbformat."""

from pathlib import Path

import nbformat

from app.parsers.base import BaseParser, ParsedDocument, ParsedSection, ParserPluginMeta


class NotebookParser(BaseParser):
    """Parse .ipynb files, producing one section per cell.

    Markdown cells become heading-bearing sections; code cells are appended
    as content under their preceding markdown heading or a default heading.
    Cell outputs are included so the RAG can answer questions about results.
    """

    def extensions(self) -> list[str]:
        return ["ipynb"]

    @classmethod
    def plugin_meta(cls) -> ParserPluginMeta:
        return ParserPluginMeta(
            name="notebook",
            display_name="parser.notebook.name",
            description="parser.notebook.desc",
            category="notebook",
            extensions=["ipynb"],
        )

    def parse(self, file_path: Path) -> ParsedDocument:
        with file_path.open("r", encoding="utf-8") as f:
            nb = nbformat.read(f, as_version=4)

        title = file_path.stem
        sections: list[ParsedSection] = []
        current: ParsedSection | None = None

        for idx, cell in enumerate(nb.cells):
            cell_type = cell.cell_type
            source = cell.source or ""

            if cell_type == "markdown":
                # First H1 in the markdown becomes the section heading
                heading = self._first_heading(source) or f"Cell {idx + 1}"
                content = source
                section = ParsedSection(
                    level=1, heading=heading, content=content, page=idx + 1,
                )
                sections.append(section)
                current = section
            elif cell_type == "code":
                code_block = f"```python\n{source}\n```"
                outputs = self._format_outputs(cell)
                content = code_block
                if outputs:
                    content += "\n\n" + outputs
                if current is not None:
                    current.content += "\n\n" + content
                else:
                    section = ParsedSection(
                        level=0, heading=f"Cell {idx + 1}", content=content, page=idx + 1,
                    )
                    sections.append(section)
                    current = section

        if not sections:
            sections.append(ParsedSection(
                level=0, heading=title, content="",
            ))

        return ParsedDocument(
            title=title, file_type="ipynb", sections=sections,
            metadata={
                "cell_count": len(nb.cells),
                "cell_types": [c.cell_type for c in nb.cells],
            },
        )

    @staticmethod
    def _first_heading(source: str) -> str:
        for line in source.split("\n"):
            stripped = line.strip()
            if stripped.startswith("#"):
                return stripped.lstrip("#").strip()
        return ""

    @staticmethod
    def _format_outputs(cell) -> str:
        parts = []
        for out in cell.get("outputs", []):
            otype = out.get("output_type", "")
            if otype == "stream":
                parts.append(out.get("text", ""))
            elif otype in ("execute_result", "display_data"):
                data = out.get("data", {})
                if "text/plain" in data:
                    text = data["text/plain"]
                    if isinstance(text, list):
                        text = "".join(text)
                    parts.append(text)
                elif "text/html" in data:
                    html = data["text/html"]
                    if isinstance(html, list):
                        html = "".join(html)
                    # Strip HTML tags for cleaner text
                    import re
                    parts.append(re.sub(r"<[^>]+>", "", html))
            elif otype == "error":
                parts.append("\n".join(out.get("traceback", [])))
        return "\n".join(p for p in parts if p).strip()
