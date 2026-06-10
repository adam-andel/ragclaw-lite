"""Check backend data state."""
import httpx

# Check KBs
r = httpx.get('http://127.0.0.1:8000/api/kb')
kbs = r.json()
print(f'Knowledge bases: {len(kbs)}')
for kb in kbs:
    print(f'  {kb["name"]} ({kb["id"][:8]})')
    r2 = httpx.get(f'http://127.0.0.1:8000/api/documents?kb_id={kb["id"]}')
    docs = r2.json()
    print(f'    Documents: {len(docs)}')
    for d in docs:
        print(f'      {d["filename"]} status={d["status"]} chunks={d["chunk_count"]}')
