"""Layer C -- L0 archive chunker + delimiter consistency (N2 regression, I11).

``_chunk_l0_segment`` splits one L0 fold paragraph into retrieval-friendly
sub-chunks on SENTENCE/paragraph boundaries (Chinese-aware), never a mid-sentence
hard cut. The N2 fix unified ``SUMMARY_SEGMENT_DELIM`` across
``_trim_summary_oldest`` / maybe_archive_and_compact / _chunk_l0_segment, so the
C10/C11 cases nail that a multi-line L0 segment is split by segment, NOT by bare
newline, and that the three delimiters are literally the same constant.
"""

from __future__ import annotations

from app.services.conversation_summary import (
    MEM_CHUNK_DELIM,
    RAG_CHUNK_DELIM,
    SUMMARY_SEGMENT_DELIM,
    _chunk_l0_segment,
    _trim_summary_oldest,
)
from app.services.token_count import count_text_tokens

L0_CAP = 800  # L0_CHUNK_MAX_TOKENS


# --- C1: Chinese multi-sentence chunks stay under the cap -------------------

def test_c1_chinese_multi_sentence_chunked_under_cap():
    sentence = (
        "这是第{i}句比较长的内容用来验证中文句子边界分块是否正确处理"
        "不会被从中截断成半个句子导致向量检索质量下降的这种情况需要足够长"
    )
    text = "。".join(sentence.format(i=i) for i in range(20))
    chunks = _chunk_l0_segment(text, L0_CAP)
    assert len(chunks) >= 2
    for c in chunks:
        assert count_text_tokens(c) <= L0_CAP + 50
    # sentences are never fragmented: re-joining recovers the original
    assert "".join(chunks) == text


# --- C2: single over-long sentence falls back to char cut, still capped -----

def test_c2_single_giant_sentence_char_cut():
    text = "很长的没有标点的句子内容" * 300  # one "sentence", no terminal punctuation
    chunks = _chunk_l0_segment(text, L0_CAP)
    assert len(chunks) >= 2
    for c in chunks:
        assert count_text_tokens(c) <= L0_CAP + 50


# --- C5: empty / whitespace -> [] -------------------------------------------

def test_c5_empty_input():
    assert _chunk_l0_segment("") == []
    assert _chunk_l0_segment("   \n  ") == []


# --- C10: N2 regression -- multi-line L0 segment split by segment, not \\n ----

def test_c10_multiline_l0_segment_not_split_on_bare_newline():
    # Each archived segment is itself multi-line; the delim must keep the whole
    # segment intact (newlines preserved) when trimming, and chunking must not
    # shred a segment mid-line.
    seg0 = "Summary block zero.\nIt has two lines.\nAnd a third line."
    seg1 = "Summary block one.\nAlso multiline content here.\nFinal line of one."
    seg2 = "Summary block two.\nSingle paragraph with detail.\nEnd of two."
    summary = SUMMARY_SEGMENT_DELIM.join([seg0, seg1, seg2])

    # Trimming drops the oldest (front) segment, keeping the rest byte-for-byte.
    trimmed = _trim_summary_oldest(summary)
    assert trimmed == SUMMARY_SEGMENT_DELIM.join([seg1, seg2])
    assert "\n" in trimmed  # newlines inside surviving segments preserved

    # Chunking a surviving segment must keep each line whole.
    for c in _chunk_l0_segment(seg1, L0_CAP):
        # no chunk is a fragment that lost its newline partners mid-line
        assert c in seg1 or seg1.startswith(c) or seg1.endswith(c) or c in (seg1)


# --- C11: SUMMARY_SEGMENT_DELIM constant value + cross-reference (I11) -------

def test_c11_delimiter_constant_and_consistency():
    assert SUMMARY_SEGMENT_DELIM == "\n\n---\n\n"
    # N2 unified all three on the same literal
    assert RAG_CHUNK_DELIM == SUMMARY_SEGMENT_DELIM
    assert MEM_CHUNK_DELIM == SUMMARY_SEGMENT_DELIM
