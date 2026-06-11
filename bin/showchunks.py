import sqlite3
db = sqlite3.connect(r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db")
db.row_factory = sqlite3.Row

# All KBs with their IDs
kbs = db.execute("SELECT id, name FROM knowledge_bases").fetchall()
for k in kbs:
    docs = db.execute("SELECT COUNT(*) FROM documents WHERE kb_id = ?", (k['id'],)).fetchone()
    print(f"KB: {k['name']} id={k['id'][:8]} docs={docs[0]}")

# All docs  
docs = db.execute("SELECT filename, kb_id FROM documents").fetchall()
for d in docs:
    print(f"  doc: {d['filename']} kb={d['kb_id'][:8]}")

# All chunks
chunks = db.execute("""
  SELECT c.chunk_index, c.heading, c.token_count, c.content, c.doc_id, d.kb_id
  FROM chunks c JOIN documents d ON c.doc_id = d.id
""").fetchall()
print(f"\nChunks: {len(chunks)}")
for c in chunks:
    print(f"  kb={c['kb_id'][:8]} idx={c['chunk_index']} heading={c['heading']} tokens={c['token_count']}")
    print(f"    {c['content'][:300]}")

db.close()
