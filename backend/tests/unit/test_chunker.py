"""Unit tests for structure-based chunker."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.parsers.base import ParsedDocument, ParsedSection
from app.services.chunker import chunker_service
from app.config import settings


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _doc(sections: list[ParsedSection], title: str = "Test") -> ParsedDocument:
    return ParsedDocument(title=title, file_type="md", sections=sections)


def _section(heading: str, content: str, level: int = 1, page: int | None = None) -> ParsedSection:
    return ParsedSection(level=level, heading=heading, content=content, page=page)


def _long_text(words: int = 500) -> str:
    """Generate text that exceeds max_tokens."""
    # English words average ~1.3 tokens each; 500 words ≈ 650 tokens.
    # For Chinese, each char is ~2 tokens with cl100k_base → 400 chars ≈ 800 tokens.
    return " ".join(["word" + str(i) for i in range(words)])


# ---------------------------------------------------------------------------
# Normal chunking
# ---------------------------------------------------------------------------

class TestChunkerNormal:
    """Basic chunking behaviour."""

    def test_multiple_sections_produce_chunks(self):
        """Multiple sections should produce at least as many chunks as sections."""
        doc = _doc([
            _section("Intro", "Some introductory content about the system." * 20),
            _section("Design", "Design considerations and architecture decisions." * 20),
            _section("Implementation", "Implementation details for the backend system." * 20),
        ])
        chunks = chunker_service.chunk(doc)
        assert len(chunks) >= 3

    def test_each_chunk_has_token_count(self):
        doc = _doc([_section("S1", "Content with reasonable length for testing." * 30)])
        chunks = chunker_service.chunk(doc)
        for c in chunks:
            assert c["token_count"] > 0

    def test_chunk_index_incremental(self):
        doc = _doc([
            _section("A", "Paragraph one." * 60),
            _section("B", "Paragraph two." * 60),
        ])
        chunks = chunker_service.chunk(doc)
        indices = [c["chunk_index"] for c in chunks]
        assert indices == list(range(len(chunks)))

    def test_heading_preserved(self):
        doc = _doc([
            _section("Architecture", "Architecture content here." * 40),
            _section("Deployment", "Deployment content here." * 40),
        ])
        chunks = chunker_service.chunk(doc)
        headings = {c["heading"] for c in chunks}
        assert "Architecture" in headings
        assert "Deployment" in headings

    def test_token_count_under_max(self):
        """Each chunk should be ≤ max_tokens * 1.5 (generous margin)."""
        long_content = "This is a reasonably sized paragraph for chunk testing. " * 200
        doc = _doc([_section("Long Section", long_content)])
        chunks = chunker_service.chunk(doc)
        limit = int(settings.chunk_max_tokens * 1.5)
        for c in chunks:
            assert c["token_count"] <= limit, f"Chunk {c['chunk_index']} has {c['token_count']} tokens > {limit}"


# ---------------------------------------------------------------------------
# Short content merging
# ---------------------------------------------------------------------------

class TestChunkerMerge:
    """Short chunk merging logic."""

    def test_short_chunks_same_heading_merged(self):
        """Adjacent short chunks with same heading should merge."""
        short_text = "Short."  # very few tokens
        doc = _doc([
            _section("A", short_text),
            _section("A", short_text),
            _section("A", short_text),
        ])
        chunks = chunker_service.chunk(doc)
        # Three tiny sections with same heading should merge into fewer chunks
        assert len(chunks) < 3


# ---------------------------------------------------------------------------
# Long content splitting
# ---------------------------------------------------------------------------

class TestChunkerSplit:
    """Oversized chunk splitting."""

    def test_long_content_splits(self):
        """A single section exceeding max_tokens should split into multiple chunks."""
        huge = _long_text(1200)  # ~1500 tokens, well over 800
        doc = _doc([_section("Huge", huge)])
        chunks = chunker_service.chunk(doc)
        assert len(chunks) > 1


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestChunkerEdgeCases:
    """Empty / degenerate inputs."""

    def test_empty_document_returns_empty(self):
        doc = _doc([])
        chunks = chunker_service.chunk(doc)
        assert chunks == []
