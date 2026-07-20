"""Quick smoke test for document parsers.

Place test files in data/test_docs/ and run:
    python -m pytest backend/tests/test_parsers.py -v
"""

import sys
from pathlib import Path

# Add backend to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.parsers.base import ParsedDocument
from app.services.parser import parser_service


TEST_DOCS_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "test_docs"


def create_test_files():
    """Create sample test documents if they don't exist."""
    TEST_DOCS_DIR.mkdir(parents=True, exist_ok=True)

    # Markdown test
    md_path = TEST_DOCS_DIR / "test.md"
    if not md_path.exists():
        md_path.write_text("""# 系统架构设计文档

## 第一章 概述

本文档描述微服务架构的整体设计方案。

### 1.1 背景

随着业务增长，单体应用已无法满足扩展需求。

### 1.2 目标

- 实现服务解耦
- 支持独立部署
- 保证高可用

## 第二章 核心设计

### 2.1 服务间通信

微服务间通信采用 **gRPC** 协议。
相比 REST API，gRPC 基于 Protobuf 序列化，
延迟降低约 60%。

```python
# gRPC 示例
service UserService {
    rpc GetUser(UserRequest) returns (UserResponse);
}
```

### 2.2 数据库设计

每个微服务拥有独立的数据库实例，
避免跨服务数据耦合。
""", encoding="utf-8")

    # TXT test
    txt_path = TEST_DOCS_DIR / "test.txt"
    if not txt_path.exists():
        txt_path.write_text("""RAGClaw 项目开发计划

第一阶段：基础架构搭建
完成 FastAPI 项目初始化、数据库模型设计、
文档解析器和分块引擎的实现。

第二阶段：检索引擎开发
实现混合检索、BM25 关键词检索、
RRF 融合排序和对话问答链路。

第三阶段：前端与部署
完成 Vue3 管理后台开发、
Docker 容器化部署和端到端测试。
""", encoding="utf-8")

    return md_path, txt_path


def test_markdown_parser():
    md_path, _ = create_test_files()
    doc = parser_service.parse(md_path, "md")
    assert isinstance(doc, ParsedDocument)
    assert doc.title == "系统架构设计文档"
    assert len(doc.sections) >= 4
    # First section should be H1
    assert doc.sections[0].heading == "系统架构设计文档"
    print(f"  ✅ Markdown: {len(doc.sections)} sections, title='{doc.title}'")


def test_txt_parser():
    _, txt_path = create_test_files()
    doc = parser_service.parse(txt_path, "txt")
    assert isinstance(doc, ParsedDocument)
    assert len(doc.sections) >= 1
    print(f"  ✅ TXT: {len(doc.sections)} sections")


def test_chunker():
    md_path, _ = create_test_files()
    doc = parser_service.parse(md_path, "md")
    from app.services.chunker import chunker_service
    chunks = chunker_service.chunk(doc)
    assert len(chunks) > 0
    for c in chunks:
        assert "content" in c
        assert "token_count" in c
        assert c["token_count"] > 0
    print(f"  ✅ Chunker: {len(chunks)} chunks from {len(doc.sections)} sections")


if __name__ == "__main__":
    print("Running parser tests...\n")
    test_markdown_parser()
    test_txt_parser()
    test_chunker()
    print("\n🎉 All tests passed!")
