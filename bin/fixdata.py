import sqlite3
db = sqlite3.connect(r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db")
db.row_factory = sqlite3.Row

# Fix admin role
db.execute("UPDATE users SET role = 'ADMIN' WHERE username = 'admin'")
db.commit()

# Fix KB null columns
db.execute("UPDATE knowledge_bases SET tenant_id = 'default', owner_id = 'default' WHERE tenant_id IS NULL")
db.commit()

# Check docs
docs = db.execute("SELECT filename, status, kb_id FROM documents").fetchall()
print(f"Documents: {len(docs)}")
for d in docs:
    print(f"  [{d['status']}] {d['filename']}  kb={d['kb_id'][:8]}")
    if d['status'] == 'completed':
        chunks = db.execute("SELECT COUNT(*) FROM chunks WHERE doc_id IN (SELECT id FROM documents WHERE filename=?)", (d['filename'],)).fetchone()
        print(f"     chunks: {chunks[0]}")

# Check chunks total
total = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
print(f"\nTotal chunks: {total}")

# Users
users = db.execute("SELECT username, role, is_active FROM users").fetchall()
print(f"\nUsers:")
for u in users:
    print(f"  {u['username']:15} role={u['role']:6} active={u['is_active']}")

db.close()
print("\nDone. Now restart backend.")
