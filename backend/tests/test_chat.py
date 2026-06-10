"""Test chat streaming directly."""
import httpx, json

base = 'http://127.0.0.1:8000/api'

# Get KBs
r = httpx.get(f'{base}/kb')
kbs = r.json()
print(f'KBs: {len(kbs)}')
kb_id = kbs[1]['id'] if len(kbs) > 1 else (kbs[0]['id'] if kbs else None)
print(f'Using KB: {kb_id[:8] if kb_id else "NONE"}')

if not kb_id:
    print('No KB found! Upload a document first.')
    exit()

# Check config
r = httpx.get(f'{base}/health')
print(f'Health: {r.json()}')

# Test chat stream
print('\n=== SSE stream ===')
with httpx.stream(
    'POST', f'{base}/chat/stream',
    json={'query': '介绍ERAG的技术栈', 'kb_id': kb_id},
    timeout=60,
) as r:
    for line in r.iter_lines():
        if line.startswith('data: '):
            data = json.loads(line[6:])
            t = data.get('type', '?')
            if t == 'token':
                print(data['content'], end='', flush=True)
            elif t == 'citation':
                print(f'\n  [引用] {data["citation"]["doc_name"]} score={data["citation"]["score"]}')
            elif t == 'error':
                print(f'\n❌ ERROR: {data["message"]}')
            elif t == 'done':
                print(f'\n✅ Done (cache_hit={data.get("cache_hit")})')
