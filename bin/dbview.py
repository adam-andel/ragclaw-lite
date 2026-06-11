"""Quick database viewer."""
import sqlite3, os

db_path = r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db"

if not os.path.exists(db_path):
    print(f"Database not found: {db_path}")
    exit(1)

db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

# All tables
tables = db.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
print(f"=== Tables ({len(tables)}) ===")
for t in tables:
    print(f"  {t['name']}")

# Users
print(f"\n=== Users ===")
users = db.execute("SELECT id, username, display_name, role, is_active, tenant_id, created_at FROM users").fetchall()
for u in users:
    role_tag = "admin" if u['role'] == 'admin' else "user "
    print(f"  [{role_tag}] {u['username']:<15} active={u['is_active']} tenant={u['tenant_id'][:8]}  {u['created_at']}")

# Knowledge bases
print(f"\n=== Knowledge Bases ===")
kbs = db.execute("SELECT id, name, tenant_id, owner_id, created_at FROM knowledge_bases").fetchall()
for k in kbs:
    print(f"  {k['name']:<20} tenant={k['tenant_id'][:8]}  owner={k['owner_id'][:8]}")

# Documents
print(f"\n=== Documents ===")
docs = db.execute("SELECT id, kb_id, filename, status, chunk_count FROM documents").fetchall()
for d in docs:
    print(f"  [{d['status']}] {d['filename']:<30} chunks={d['chunk_count']}")

# Stats
doc_count = db.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
user_count = db.execute("SELECT COUNT(*) FROM users").fetchone()[0]
chunk_count = db.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
print(f"\n=== Summary: {user_count} users, {doc_count} docs, {chunk_count} chunks ===")

db.close()
