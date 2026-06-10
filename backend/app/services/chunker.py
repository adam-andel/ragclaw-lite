"""Structure-based document chunking algorithm.

Splits parsed documents into chunks based on their heading tree structure,
keeping each chunk semantically coherent (same section together).
"""

import tiktoken
from app.parsers.base import ParsedDocument, ParsedSection
from app.config import settings


class StructureChunker:
    """Chunk documents by heading structure, controlling chunk size."""

    def __init__(self):
        # Use cl100k_base as a generic tokenizer (works for Chinese too, roughly)
        try:
            self._tokenizer = tiktoken.get_encoding("cl100k_base")
        except Exception:
            self._tokenizer = None

    def chunk(self, doc: ParsedDocument) -> list[dict]:
        """Chunk a parsed document into structured chunks.

        Returns:
            List of chunk dicts with keys:
                content, token_count, heading, page, chunk_index
        """
        all_chunks: list[dict] = []

        for section in doc.sections:
            section_chunks = self._chunk_section(section)
            all_chunks.extend(section_chunks)

        # Merge very short adjacent chunks (same heading)
        merged = self._merge_short_chunks(all_chunks)

        # Re-index
        for i, chunk in enumerate(merged):
            chunk["chunk_index"] = i

        return merged

    def _chunk_section(self, section: ParsedSection) -> list[dict]:
        """Split a single section into chunks."""
        content = section.content
        token_count = self._count_tokens(content)

        # If content fits in one chunk, return as-is
        if token_count <= settings.chunk_max_tokens:
            return [{
                "content": content,
                "token_count": token_count,
                "heading": section.heading,
                "page": section.page,
                "chunk_index": 0,
            }]

        # Split by paragraphs (double newline)
        paragraphs = content.split("\n\n")
        chunks: list[dict] = []
        current_text = ""
        current_tokens = 0

        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            para_tokens = self._count_tokens(para)

            # If adding this paragraph exceeds max, flush current chunk
            if current_tokens + para_tokens > settings.chunk_max_tokens and current_tokens >= settings.chunk_min_tokens:
                chunks.append({
                    "content": current_text.strip(),
                    "token_count": current_tokens,
                    "heading": section.heading,
                    "page": section.page,
                    "chunk_index": len(chunks),
                })
                current_text = para
                current_tokens = para_tokens
            else:
                if current_text:
                    current_text += "\n\n" + para
                else:
                    current_text = para
                current_tokens += para_tokens

        # Don't forget the last one
        if current_text.strip():
            chunks.append({
                "content": current_text.strip(),
                "token_count": current_tokens,
                "heading": section.heading,
                "page": section.page,
                "chunk_index": len(chunks),
            })

        return chunks

    def _merge_short_chunks(self, chunks: list[dict]) -> list[dict]:
        """Merge adjacent chunks that are too short and share the same heading."""
        if len(chunks) <= 1:
            return chunks

        merged: list[dict] = [chunks[0]]

        for chunk in chunks[1:]:
            last = merged[-1]

            # Merge if same heading and combined size fits
            if (last["heading"] == chunk["heading"]
                    and last["token_count"] + chunk["token_count"] <= settings.chunk_max_tokens):
                last["content"] += "\n\n" + chunk["content"]
                last["token_count"] += chunk["token_count"]
            else:
                merged.append(chunk)

        return merged

    def _count_tokens(self, text: str) -> int:
        """Estimate token count. Falls back to character-based estimate."""
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        # Rough estimate: Chinese ~1.5 chars/token, English ~4 chars/token
        return max(1, len(text) // 3)


# Singleton
chunker_service = StructureChunker()
