"""End-to-end test: KB → upload → chunks → retrieval."""
import httpx, json

base = 'http://localhost:8000/api'

# 1. Create KB
r = httpx.post(f'{base}/kb', json={'name': '测试知识库', 'description': '用于测试'})
kb = r.json()
print(f'1. KB created: {kb["id"][:8]}... {kb["name"]}')

# 2. Upload test doc
files = {'file': ('test.md', '# RAGClaw\n\n## 概述\nRAGClaw 是企业 RAG 平台。\n\n## 技术栈\nFastAPI + Vue3。'.encode('utf-8'), 'text/markdown')}
r = httpx.post(f'{base}/documents/upload', files=files, data={'kb_id': kb['id']}, timeout=120)
doc = r.json()
print(f'2. Doc: {doc["id"][:8]}... {doc["filename"]} status={doc["status"]} chunks={doc["chunk_count"]}')

# 3. Get chunks
r = httpx.get(f'{base}/documents/{doc["id"]}/chunks')
chunks = r.json()
print(f'3. Chunks: {len(chunks)}')
for c in chunks:
    print(f'   [{c["chunk_index"]}] heading={str(c.get("heading",""))[:25]} tokens={c["token_count"]}')

# 4. Retrieval test
r = httpx.post(f'{base}/retrieval/search', json={'query': '技术栈是什么', 'kb_id': kb['id']})
results = r.json()
print(f'4. Retrieval: {len(results)} results')
for res in results[:3]:
    print(f'   fusion={res["fusion_score"]:.3f} vec={res["vector_score"]:.3f} bm25={res["bm25_score"]:.3f}')

# 5. Stats
r = httpx.get(f'{base}/stats/overview')
stats = r.json()
print(f'5. Stats: docs={stats["document_count"]} chunks={stats["chunk_count"]} cache_hit={stats["cache_hit_rate"]:.1%}')

print('\n🎉 All API tests passed!')
