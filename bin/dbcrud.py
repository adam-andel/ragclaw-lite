"""Quick database viewer."""
import sqlite3, os

db_path = r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db"

if not os.path.exists(db_path):
    print(f"Database not found: {db_path}")
    exit(1)

db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

# Users
# print(f"\n=== Users ===")

# users = db.execute("UPDATE users SET role=\"admin\" WHERE username=\"admin\"")
# users = db.execute("SELECT id, username, display_name, role, is_active, tenant_id, created_at FROM users").fetchall()
# for u in users:
#     role_tag = "admin" if u['role'] == 'admin' else "user "
#     print(f"  [{role_tag}] {u['username']:<15} active={u['is_active']} tenant={u['tenant_id'][:8]}  {u['created_at']}")

conversations = db.execute("DELETE FROM conversations WHERE user_id IS NULL")

# r = db.execute("SELECT id FROM users WHERE username='admin'").fetchone()
# db.execute('UPDATE conversations SET user_id = ? WHERE user_id IS NULL', (r[0],))
# db.commit()
