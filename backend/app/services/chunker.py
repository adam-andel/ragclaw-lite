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

            # If a single paragraph is too large, split it into sub-chunks
            if para_tokens > settings.chunk_max_tokens:
                sub_chunks = self._split_large_text(para, section)
                # Flush any accumulated content first
                if current_text:
                    chunks.append({
                        "content": current_text.strip(),
                        "token_count": current_tokens,
                        "heading": section.heading,
                        "page": section.page,
                        "chunk_index": len(chunks),
                    })
                    current_text = ""
                    current_tokens = 0
                chunks.extend(sub_chunks)
                continue

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

    def _split_large_text(self, text: str, section: ParsedSection) -> list[dict]:
        """Split a single paragraph that exceeds max_tokens into sub-chunks."""
        chunks = []
        # Try splitting by single newlines first
        parts = text.split("\n")
        if len(parts) <= 1:
            # No newlines: split by character count
            estimated_chars_per_chunk = settings.chunk_max_tokens * 3  # rough char→token ratio
            for start in range(0, len(text), estimated_chars_per_chunk):
                sub = text[start:start + estimated_chars_per_chunk].strip()
                if sub:
                    chunks.append({
                        "content": sub,
                        "token_count": self._count_tokens(sub),
                        "heading": section.heading,
                        "page": section.page,
                        "chunk_index": 0,
                    })
            return chunks

        # Split by lines, grouping to max_tokens
        current = ""
        current_tk = 0
        for line in parts:
            line = line.strip()
            if not line:
                continue
            tk = self._count_tokens(line)
            if current_tk + tk > settings.chunk_max_tokens and current_tk >= settings.chunk_min_tokens:
                chunks.append({
                    "content": current.strip(),
                    "token_count": current_tk,
                    "heading": section.heading,
                    "page": section.page,
                    "chunk_index": len(chunks),
                })
                current = line
                current_tk = tk
            else:
                current = (current + "\n" + line).strip() if current else line
                current_tk += tk
        if current:
            chunks.append({
                "content": current.strip(),
                "token_count": current_tk,
                "heading": section.heading,
                "page": section.page,
                "chunk_index": len(chunks),
            })
        return chunks

    def _count_tokens(self, text: str) -> int:
        """Estimate token count. Falls back to character-based estimate."""
        if self._tokenizer:
            return len(self._tokenizer.encode(text))
        # Rough estimate: Chinese ~1.5 chars/token, English ~4 chars/token
        return max(1, len(text) // 3)


# Singleton
chunker_service = StructureChunker()
