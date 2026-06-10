"""Simple upload test."""
import httpx

base = 'http://127.0.0.1:8000/api'

# Create KB
r = httpx.post(f'{base}/kb', json={'name': 'Test'})
kb = r.json()
print('KB:', kb['id'][:8])

# Upload
files = {'file': ('test.md', '# Title\n\n## Section\nSome content here.'.encode(), 'text/markdown')}
r = httpx.post(f'{base}/documents/upload', files=files, data={'kb_id': kb['id']}, timeout=120)
doc = r.json()
print('Status:', doc['status'])
print('Error:', doc.get('error_message', ''))
print('Chunks:', doc['chunk_count'])
