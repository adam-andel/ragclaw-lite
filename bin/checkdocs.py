import sqlite3
db = sqlite3.connect(r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db")
db.row_factory = sqlite3.Row

docs = db.execute("""
  SELECT d.filename, d.status, d.error_message, d.kb_id, k.name as kb_name, d.created_at
  FROM documents d LEFT JOIN knowledge_bases k ON d.kb_id = k.id
  ORDER BY d.created_at DESC
""").fetchall()

print("Documents:")
for d in docs:
    err = (d["error_message"] or "-")[:80]
    ts = d["created_at"][:19]
    print(f"  [{d['status']}] {d['kb_name']:10} | {d['filename'][:30]:30} | {ts} | err={err}")

# Also check ChromaDB
print("\nChromaDB vectors:")
import sys; sys.path.insert(0, r"D:\AI\Autoclaw\ERAG\erag\backend")
from app.services.vector_store import vector_store
kbs = db.execute("SELECT id, name FROM knowledge_bases").fetchall()
for k in kbs:
    print(f"  {k['name']:20} ({k['id'][:8]}): {vector_store.count(k['id'])} vectors")

db.close()
