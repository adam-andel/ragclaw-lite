import sqlite3
db = sqlite3.connect(r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db")
db.row_factory = sqlite3.Row
convs = db.execute("SELECT id, title, user_id, created_at FROM conversations ORDER BY created_at DESC").fetchall()
print(f"Conversations: {len(convs)}")
for c in convs:
    uid = c["user_id"] or "NULL"
    print(f"  user_id={uid:36} title={c['title'][:50]}")
db.close()
