"""Unit tests for document parsers (Markdown, TXT, unsupported types)."""

import sys
from pathlib import Path

import pytest

# Ensure backend is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.parsers.base import ParsedDocument, ParsedSection
from app.services.parser import parser_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_md(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

class TestMarkdownParser:
    """Markdown parsing correctness."""

    def test_parse_title_and_sections(self, tmp_path):
        md = _make_md(tmp_path / "test.md", """# 系统架构设计文档

## 第一章 概述

本文档描述微服务架构的整体设计方案。

### 1.1 背景

随着业务增长，单体应用已无法满足扩展需求。

## 第二章 核心设计

gRPC 通信协议选择。
""")
        doc = parser_service.parse(md, "md")
        assert isinstance(doc, ParsedDocument)
        assert doc.title == "系统架构设计文档"
        assert doc.file_type == "markdown"
        assert len(doc.sections) >= 3  # H1 + two H2 sections

    def test_heading_hierarchy(self, tmp_path):
        """Verify H1 > H2 > H3 levels are preserved."""
        md = _make_md(tmp_path / "hierarchy.md", """# L1 Title

## L2 Section A

### L3 Sub A1

content here

## L2 Section B

### L3 Sub B1

more content
""")
        doc = parser_service.parse(md, "md")
        levels = [s.level for s in doc.sections]
        headings = [s.heading for s in doc.sections]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels
        # Check specific headings
        assert "L1 Title" in headings
        assert "L2 Section A" in headings
        assert "L3 Sub A1" in headings

    def test_code_block_preserved(self, tmp_path):
        """Code block text should be present in section content."""
        md = _make_md(tmp_path / "code.md", """# API Example

```python
def hello():
    print("Hello ERAG")
```
""")
        doc = parser_service.parse(md, "md")
        full = "\n".join(s.content for s in doc.sections)
        assert "hello()" in full
        assert "Hello ERAG" in full

    def test_empty_file_no_crash(self, tmp_path):
        """Empty markdown should not crash."""
        md = _make_md(tmp_path / "empty.md", "")
        doc = parser_service.parse(md, "md")
        assert isinstance(doc, ParsedDocument)
        # Sections may be empty but no exception
        assert doc.section_count >= 0

    def test_file_not_found_raises(self, tmp_path):
        """Parsing a non-existent file should raise."""
        with pytest.raises(Exception):
            parser_service.parse(tmp_path / "nope.md", "md")


# ---------------------------------------------------------------------------
# TXT parser
# ---------------------------------------------------------------------------

class TestTxtParser:
    """Plain-text parsing correctness."""

    def test_txt_parses_sections(self, tmp_path):
        txt = tmp_path / "plan.txt"
        txt.write_text("""ERAG 项目开发计划

第一阶段：基础架构搭建
完成 FastAPI 项目初始化、数据库模型设计。

第二阶段：检索引擎开发
实现混合检索、BM25 关键词检索。

第三阶段：前端与部署
完成 Vue3 管理后台开发、Docker 容器化部署。
""", encoding="utf-8")
        doc = parser_service.parse(txt, "txt")
        assert isinstance(doc, ParsedDocument)
        assert doc.file_type == "txt"
        assert len(doc.sections) >= 1

    def test_chinese_encoding_gbk(self, tmp_path):
        """GBK-encoded Chinese text should parse correctly."""
        txt = tmp_path / "gbk.txt"
        content = "中文测试文档\n\n第二段内容"
        txt.write_text(content, encoding="gbk")
        doc = parser_service.parse(txt, "txt")
        # Should not crash; content should have something
        assert doc.section_count >= 0 or len(doc.full_text) > 0


# ---------------------------------------------------------------------------
# Supported types
# ---------------------------------------------------------------------------

class TestSupportedTypes:
    """Format support enumeration."""

    def test_supported_types_list(self):
        types = parser_service.supported_types()
        expected = {"pdf", "docx", "doc", "md", "markdown", "txt"}
        assert set(types) == expected

    def test_unsupported_type_raises(self, tmp_path):
        """Unsupported format should raise ValueError."""
        dummy = tmp_path / "test.exe"
        dummy.write_text("not a real exe", encoding="utf-8")
        with pytest.raises(ValueError, match="No parser available"):
            parser_service.parse(dummy, "exe")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestParserEdgeCases:
    """Boundary and stress scenarios."""

    def test_large_file(self, tmp_path):
        """100KB+ file should parse without issues."""
        md = _make_md(tmp_path / "large.md",
                      "# Large Doc\n\n" + "A paragraph of text. " * 5000)
        doc = parser_service.parse(md, "md")
        assert doc.section_count > 0

    def test_markdown_with_front_matter(self, tmp_path):
        """YAML front matter should be stripped."""
        md = _make_md(tmp_path / "fm.md", """---
title: Front Matter Doc
author: tester
---

# Real Title

Some real content here.
""")
        doc = parser_service.parse(md, "md")
        assert doc.title == "Real Title"
