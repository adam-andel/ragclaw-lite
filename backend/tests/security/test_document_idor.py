"""Security regression: document-read IDOR (P0) — gid / KB-group authorization.

Covers the fix that routes every document READ endpoint through
``app.routers.documents._load_doc_for_read``.

Authorization contract under test:
    A user may READ a document iff
        role == admin
        OR doc.owner_id == user.id
        OR the user is a KBUserAccess member of ANY KB that contains the doc.
    Otherwise -> 403. Unknown doc -> 404. No credentials -> 401.
    There is NO tenant gate in this internal-deployment model: only KB
    membership (gid) and ownership matter.

Endpoint shorthands used throughout (all GET, prefix ``/api/documents``):
    E1 ``/{doc_id}/download``            E4 ``/{doc_id}/chunks``
    E2 ``/{doc_id}``                     E5 ``/{doc_id}/chunks/{index}``
    E3 ``/{doc_id}/status``              E6 ``/{doc_id}/kbs``
"""

import hashlib
import sys
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from app.config import settings
from app.models.document import Document, Chunk, DocStatus, KBDocument
from app.models.kb_access import KBUserAccess
from app.models.knowledge_base import KnowledgeBase
from app.models.user import User, UserRole
from app.services.auth import create_access_token, decode_token
from app.services.config_manager import config_manager

# Test-infra shim (not part of the fix under test):
# ConfigManager auto-generates the JWT signing secret during FastAPI startup,
# but httpx ASGITransport never runs lifespan events, so the in-memory cache
# stays empty and auth.get_jwt_secret() raises RuntimeError. Seed a
# deterministic secret at import time so tokens can be signed and verified.
_TEST_JWT_SECRET = "pytest-document-idor-jwt-secret"

if not config_manager._config.get("jwt_secret"):
    config_manager._config["jwt_secret"] = _TEST_JWT_SECRET

# All six read endpoints guarded by _load_doc_for_read.
READ_ENDPOINTS = ["E1", "E2", "E3", "E4", "E5", "E6"]


@pytest.fixture(autouse=True)
def _jwt_secret_ready():
    """Re-assert the signing secret before each test (cheap and idempotent)."""
    if not config_manager._config.get("jwt_secret"):
        config_manager._config["jwt_secret"] = _TEST_JWT_SECRET
    yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _test_hash(password: str) -> str:
    """Mirror the sha256 hash form conftest monkeypatches into app.services.auth."""
    return "test$" + hashlib.sha256(password.encode()).hexdigest()


async def _make_user(role: UserRole = UserRole.USER, tenant_id: str | None = None):
    """Insert a User directly and mint a JWT for it. Returns (token, uid, tid)."""
    from app.database import async_session

    uid = str(uuid.uuid4())
    tid = tenant_id or str(uuid.uuid4())
    username = f"idor_{role.value}_{uid[:8]}"
    async with async_session() as db:
        db.add(User(
            id=uid, username=username,
            hashed_password=_test_hash("pw"),
            display_name=username, role=role,
            is_active=True, tenant_id=tid,
        ))
        await db.commit()
    return create_access_token(uid, username, role.value, tid), uid, tid


async def _make_kb(owner_uid: str | None, owner_tid: str | None = None) -> str:
    """Insert a KnowledgeBase and return its id."""
    from app.database import async_session

    kid = str(uuid.uuid4())
    async with async_session() as db:
        db.add(KnowledgeBase(
            id=kid, name=f"kb-{kid[:8]}",
            description="idor test kb",
            tenant_id=owner_tid, owner_id=owner_uid,
        ))
        await db.commit()
    return kid


async def _make_doc(
    owner_uid: str | None,
    owner_tid: str | None = None,
    kb_ids: list[str] | None = None,
    member_pairs: list[tuple[str, str]] | None = None,
    *,
    content: bytes = b"top secret payload",
    chunk_text: str = "chunk body",
    orphan: bool = False,
    status: DocStatus = DocStatus.COMPLETED,
) -> str:
    """Create a full document fixture: real file on disk + DB rows.

    ``kb_ids``       -> one KBDocument link per kb.
    ``member_pairs`` -> list of (kb_id, user_id) KBUserAccess grants.
    ``orphan``       -> force owner_id = None (pre-migration / imported doc).
    """
    from app.database import async_session

    kb_ids = kb_ids or []
    member_pairs = member_pairs or []

    doc_id = str(uuid.uuid4())
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    saved_path = settings.upload_dir / f"{doc_id}_doc.txt"
    saved_path.write_bytes(content)

    async with async_session() as db:
        db.add(Document(
            id=doc_id, filename="doc.txt", file_type="txt",
            file_size=len(content), file_path=str(saved_path),
            status=status, chunk_count=1 if chunk_text else 0, progress=100,
            owner_id=None if orphan else owner_uid,
            tenant_id=owner_tid,
        ))
        await db.flush()
        for kb_id in kb_ids:
            db.add(KBDocument(kb_id=kb_id, doc_id=doc_id))
        if chunk_text:
            db.add(Chunk(
                doc_id=doc_id, chunk_index=0,
                content=chunk_text, token_count=len(chunk_text),
            ))
        for kb_id, uid in member_pairs:
            db.add(KBUserAccess(kb_id=kb_id, user_id=uid))
        await db.commit()
    return doc_id


async def _hit(client, ep: str, doc_id: str, headers: dict | None):
    """Issue the GET request for endpoint shorthand E1..E6."""
    paths = {
        "E1": f"/api/documents/{doc_id}/download",
        "E2": f"/api/documents/{doc_id}",
        "E3": f"/api/documents/{doc_id}/status",
        "E4": f"/api/documents/{doc_id}/chunks",
        "E5": f"/api/documents/{doc_id}/chunks/0",
        "E6": f"/api/documents/{doc_id}/kbs",
    }
    return await client.get(paths[ep], headers=headers or {})


@pytest.fixture
async def actors(admin_token, moderator_token, user_token, user2_token):
    """Decoded identities for the four conftest role fixtures."""
    def _pack(token):
        payload = decode_token(token)
        return {"token": token, "uid": payload["sub"], "tid": payload.get("tenant_id")}

    return {
        "admin": _pack(admin_token),
        "mod": _pack(moderator_token),
        "user": _pack(user_token),
        "user2": _pack(user2_token),
    }


# ===========================================================================
# A1..A11 — authorization matrix
# ===========================================================================

class TestGidReadAuthorization:

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a1_kb_member_reads_group_doc(self, client, actors, ep):
        """A1: member of a KB that contains the doc -> 200 on every read endpoint."""
        admin, user = actors["admin"], actors["user"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(
            admin["uid"], admin["tid"], [kb_id], [(kb_id, user["uid"])]
        )
        r = await _hit(client, ep, doc_id, _auth(user["token"]))
        assert r.status_code == 200, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a2_admin_reads_arbitrary_doc(self, client, actors, ep):
        """A2: admin is a superuser — reads a doc it neither owns nor shares."""
        admin, user = actors["admin"], actors["user"]
        doc_id = await _make_doc(user["uid"], user["tid"])
        r = await _hit(client, ep, doc_id, _auth(admin["token"]))
        assert r.status_code == 200, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a3_moderator_not_blanket_reader(self, client, actors, ep):
        """A3: moderator is NOT a blanket reader — non-owner, non-member -> 403."""
        admin, mod = actors["admin"], actors["mod"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(admin["uid"], admin["tid"], [kb_id])
        r = await _hit(client, ep, doc_id, _auth(mod["token"]))
        assert r.status_code == 403, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a4_non_member_blocked(self, client, actors, ep):
        """A4 (core IDOR): outsider guessing a doc_id is blocked with 403."""
        admin, user2 = actors["admin"], actors["user2"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(admin["uid"], admin["tid"], [kb_id])
        r = await _hit(client, ep, doc_id, _auth(user2["token"]))
        assert r.status_code == 403, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a5_member_of_unrelated_kb_blocked(self, client, actors, ep):
        """A5: membership in some OTHER KB grants nothing for this doc."""
        admin, user = actors["admin"], actors["user"]
        kb_with_doc = await _make_kb(admin["uid"], admin["tid"])
        kb_other = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(
            admin["uid"], admin["tid"], [kb_with_doc], [(kb_other, user["uid"])]
        )
        r = await _hit(client, ep, doc_id, _auth(user["token"]))
        assert r.status_code == 403, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a6_membership_in_any_containing_kb_suffices(self, client, actors, ep):
        """A6: doc linked to two KBs, user is member of only the second -> 200."""
        admin, user = actors["admin"], actors["user"]
        kb_a = await _make_kb(admin["uid"], admin["tid"])
        kb_b = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(
            admin["uid"], admin["tid"], [kb_a, kb_b], [(kb_b, user["uid"])]
        )
        r = await _hit(client, ep, doc_id, _auth(user["token"]))
        assert r.status_code == 200, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a7_owner_safety_net(self, client, actors, ep):
        """A7: owner of an unlinked doc keeps access even with zero KB memberships."""
        user = actors["user"]
        doc_id = await _make_doc(user["uid"], user["tid"])
        r = await _hit(client, ep, doc_id, _auth(user["token"]))
        assert r.status_code == 200, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a8_unknown_doc_returns_404(self, client, actors, ep):
        """A8: a doc_id that does not exist -> 404 (not 403, not 500)."""
        user = actors["user"]
        r = await _hit(client, ep, str(uuid.uuid4()), _auth(user["token"]))
        assert r.status_code == 404, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a9_unauthenticated_returns_401(self, client, actors, ep):
        """A9: no Authorization header -> 401 before any DB lookup."""
        admin = actors["admin"]
        doc_id = await _make_doc(admin["uid"], admin["tid"])
        r = await _hit(client, ep, doc_id, None)
        assert r.status_code == 401, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a10_orphan_doc_fail_closed(self, client, actors, ep):
        """A10: owner_id IS NULL must never be treated as 'owned by caller'."""
        user = actors["user"]
        doc_id = await _make_doc(None, None, orphan=True)
        r = await _hit(client, ep, doc_id, _auth(user["token"]))
        assert r.status_code == 403, f"{ep} -> {r.status_code} {r.text[:200]}"

    @pytest.mark.parametrize("ep", READ_ENDPOINTS)
    async def test_a11_orphan_doc_admin_recovery(self, client, actors, ep):
        """A11: admin can still reach orphan docs so they remain recoverable."""
        admin = actors["admin"]
        doc_id = await _make_doc(None, None, orphan=True)
        r = await _hit(client, ep, doc_id, _auth(admin["token"]))
        assert r.status_code == 200, f"{ep} -> {r.status_code} {r.text[:200]}"


# ===========================================================================
# P1..P6 — payload contracts for an authorized caller
# ===========================================================================

class TestEndpointPayloads:
    """The fix must not change the response shape for legitimate readers."""

    @pytest.fixture
    async def shared(self, actors):
        """A doc owned by admin, linked to one KB, with `user` as KB member."""
        admin, user = actors["admin"], actors["user"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        content = b"payload-under-test"
        doc_id = await _make_doc(
            admin["uid"], admin["tid"], [kb_id], [(kb_id, user["uid"])],
            content=content, chunk_text="chunk body alpha",
        )
        return {
            "kb_id": kb_id, "doc_id": doc_id, "content": content,
            "owner_id": admin["uid"], "headers": _auth(user["token"]),
        }

    async def test_p1_download_streams_original_bytes(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}/download", headers=shared["headers"]
        )
        assert r.status_code == 200
        assert r.content == shared["content"]
        assert "attachment" in r.headers.get("content-disposition", "")

    async def test_p2_detail_exposes_owner_and_kb_links(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}", headers=shared["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == shared["doc_id"]
        assert shared["kb_id"] in body["kb_ids"]
        assert body["owner_id"] == shared["owner_id"]

    async def test_p3_status_shape(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}/status", headers=shared["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == shared["doc_id"]
        assert "status" in body
        assert isinstance(body["chunk_count"], int)

    async def test_p4_chunks_list_and_search_filter(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}/chunks", headers=shared["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        assert body["total"] >= 1
        assert len(body["items"]) >= 1

        r2 = await client.get(
            f"/api/documents/{shared['doc_id']}/chunks",
            params={"search": "zzznomatch"}, headers=shared["headers"],
        )
        assert r2.status_code == 200
        assert r2.json()["total"] == 0

    async def test_p5_single_chunk_and_missing_index(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}/chunks/0", headers=shared["headers"]
        )
        assert r.status_code == 200
        assert r.json()["chunk_index"] == 0

        r2 = await client.get(
            f"/api/documents/{shared['doc_id']}/chunks/99999", headers=shared["headers"]
        )
        assert r2.status_code == 404
        assert "CHUNK_NOT_FOUND" in r2.text

    async def test_p6_kbs_returns_linked_ids(self, client, shared):
        r = await client.get(
            f"/api/documents/{shared['doc_id']}/kbs", headers=shared["headers"]
        )
        assert r.status_code == 200
        body = r.json()
        assert isinstance(body, list)
        assert shared["kb_id"] in body


# ===========================================================================
# R1..R7 — neighbouring endpoints must stay correct after the fix
# ===========================================================================

class TestRegression:

    async def test_r1_delete_denied_for_outsider(self, client, actors):
        """R1: DELETE stays owner/staff-only — an outsider gets 403, doc survives."""
        admin, user2 = actors["admin"], actors["user2"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(admin["uid"], admin["tid"], [kb_id])

        r = await client.delete(f"/api/documents/{doc_id}", headers=_auth(user2["token"]))
        assert r.status_code == 403

        # The document must still be readable by admin -> nothing was deleted.
        check = await client.get(f"/api/documents/{doc_id}", headers=_auth(admin["token"]))
        assert check.status_code == 200

    async def test_r2_kb_documents_listing_scoped(self, client, actors):
        """R2: /api/kb/{kb_id}/documents — owner sees it, outsider gets 403."""
        admin, user2 = actors["admin"], actors["user2"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(admin["uid"], admin["tid"], [kb_id])

        r = await client.get(f"/api/kb/{kb_id}/documents", headers=_auth(admin["token"]))
        assert r.status_code == 200
        assert doc_id in [d["id"] for d in r.json()]

        r2 = await client.get(f"/api/kb/{kb_id}/documents", headers=_auth(user2["token"]))
        assert r2.status_code == 403

    async def test_r3_by_kb_listing_scoped(self, client, actors):
        """R3: legacy /api/documents/by-kb/{kb_id} enforces KB membership too."""
        admin, user2 = actors["admin"], actors["user2"]
        kb_id = await _make_kb(admin["uid"], admin["tid"])
        doc_id = await _make_doc(admin["uid"], admin["tid"], [kb_id])

        r = await client.get(f"/api/documents/by-kb/{kb_id}", headers=_auth(admin["token"]))
        assert r.status_code == 200
        assert doc_id in [d["id"] for d in r.json()]

        r2 = await client.get(f"/api/documents/by-kb/{kb_id}", headers=_auth(user2["token"]))
        assert r2.status_code == 403

    async def test_r5_document_list_is_owner_scoped(self, client, actors):
        """R5: GET /api/documents never leaks another user's documents."""
        user, user2 = actors["user"], actors["user2"]
        mine = await _make_doc(user["uid"], user["tid"])
        theirs = await _make_doc(user2["uid"], user2["tid"])

        r = await client.get("/api/documents", headers=_auth(user["token"]))
        assert r.status_code == 200
        ids = [d["id"] for d in r.json()["items"]]
        assert mine in ids
        assert theirs not in ids

    async def test_r7_app_imports(self):
        """R7: boot sanity — the router edits keep the ASGI app importable."""
        from app.main import app
        assert app is not None
