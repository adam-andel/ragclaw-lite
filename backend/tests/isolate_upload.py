"""Isolate the upload issue by running the pipeline directly."""
import sys, asyncio, traceback
from pathlib import Path

sys.path.insert(0, r'D:\AI\Autoclaw\RAGClaw\ragclaw\backend')

async def test():
    from app.database import init_db, async_session
    from app.models.document import Document, Chunk, DocStatus
    from app.models.knowledge_base import KnowledgeBase
    from app.services.parser import parser_service
    from app.services.chunker import chunker_service
    from app.services.vector_store import vector_store
    from app.config import settings
    import uuid

    await init_db()

    # Create KB
    async with async_session() as db:
        kid = str(uuid.uuid4())
        kb = KnowledgeBase(id=kid, name='Isolation Test')
        db.add(kb)
        await db.commit()
        print(f'KB created: {kid[:8]}')

    # Create test file
    test_file = settings.upload_dir / 'isolation_test.md'
    test_file.write_text('# Test Doc\n\n## Section 1\n\nThis is content for section one.\n\n## Section 2\n\nMore content here.', encoding='utf-8')

    steps = [
        ('Parse', lambda: parser_service.parse(test_file, 'md')),
    ]

    doc_id = str(uuid.uuid4())

    for step_name, fn in steps:
        try:
            print(f'  {step_name}... ', end='', flush=True)
            result = fn()
            print('OK')
            if step_name == 'Parse':
                parsed = result
                print(f'    Sections: {len(parsed.sections)}')
        except Exception as e:
            print(f'FAILED: {e}')
            traceback.print_exc()

    # Test chunking
    try:
        print('  Chunk... ', end='', flush=True)
        chunks = chunker_service.chunk(parsed)
        print(f'OK ({len(chunks)} chunks)')
        for c in chunks:
            print(f'    [{c["chunk_index"]}] {c["heading"]}: {c["content"][:50]}...')
    except Exception as e:
        print(f'FAILED: {e}')
        traceback.print_exc()

    # Test embedding
    try:
        print('  Embed + Vector Store... ', end='', flush=True)
        chunk_dicts = []
        for i, c in enumerate(chunks):
            cid = str(uuid.uuid4())
            chunk_dicts.append({
                'id': cid, 'content': c['content'], 'token_count': c['token_count'],
                'heading': c['heading'], 'page': c['page'], 'chunk_index': i,
                'doc_id': doc_id,
            })
        vector_store.add_chunks(kid, chunk_dicts)
        print('OK')
    except Exception as e:
        print(f'FAILED: {e}')
        traceback.print_exc()

    print('\nDone!')

asyncio.run(test())
