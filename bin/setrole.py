"""Set user role (run with backend STOPPED)."""
import sqlite3, sys

db_path = r"D:\AI\Autoclaw\ERAG\erag\data\sqlite\erag.db"
username = sys.argv[1] if len(sys.argv) > 1 else "admin"
role = sys.argv[2] if len(sys.argv) > 2 else "admin"

db = sqlite3.connect(db_path)
db.row_factory = sqlite3.Row

# Check current
u = db.execute("SELECT id, username, role FROM users WHERE username = ?", (username,)).fetchone()
if not u:
    print(f"User '{username}' not found")
    db.close()
    exit(1)

print(f"Before: {u['username']} role={u['role']}")

# Update
db.execute("UPDATE users SET role = ? WHERE username = ?", (role.upper(), username))
db.commit()

# Verify
u2 = db.execute("SELECT username, role FROM users WHERE username = ?", (username,)).fetchone()
print(f"After:  {u2['username']} role={u2['role']}")
print("Done. Restart backend and re-login to take effect.")

db.close()
