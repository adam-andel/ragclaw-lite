"""Test upload to mybase directly."""
import httpx

# First login to get token
r = httpx.post('http://127.0.0.1:8000/api/auth/login', json={'username': 'admin', 'password': 'admin'})
if r.status_code != 200:
    print(f"Login failed: {r.status_code} {r.text[:200]}")
    exit()
token = r.json()['access_token']
print(f"Logged in as admin")

# Upload test file
kb_id = "2ea92cfb"
files = {'file': ('test_upload.txt', b'# MyBase Test\n\n## Content\nThis is a test document.', 'text/plain')}
r = httpx.post(
    'http://127.0.0.1:8000/api/documents/upload',
    files=files,
    data={'kb_id': kb_id},
    headers={'Authorization': f'Bearer {token}'},
    timeout=120,
)
print(f"Upload status: {r.status_code}")
doc = r.json()
print(f"  Result: status={doc.get('status')} chunks={doc.get('chunk_count')} error={doc.get('error_message', '')[:200]}")
