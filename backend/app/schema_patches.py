"""Idempotent schema patches — the escape hatch for everything ``create_all`` cannot do.

``Base.metadata.create_all(checkfirst=True)`` only creates *whole missing tables*.
It never touches a table that already exists, so it cannot add a column, drop a
column, change a type, rebuild an index, or backfill data. Every such change is
expressed here as a **patch**.

Design rules
------------
1. **Every patch is idempotent.** A patch must be safe to run on every startup,
   forever, on any database (fresh or years old). This is enforced structurally:
   each patch supplies an ``applied`` predicate that inspects the *live schema*
   (not a version number) to decide whether the work is still needed. There is no
   version table, no revision chain, no ordering constraint between stacks — which
   is exactly why multiple stacks can evolve the schema independently.

2. **Guard on state, never on history.** Write ``has_column(...)`` /
   ``has_index(...)`` style checks. Never write "if version < N", because a
   sibling stack may have applied a different subset of patches.

3. **A patch is forever.** Once merged, a patch stays in this list. Deleting one
   breaks any database that has not yet applied it. Patches are cheap: a skipped
   patch costs one ``PRAGMA``/catalog lookup per boot.

4. **Fresh installs are handled too.** On a brand-new database ``create_all``
   already builds the final shape from the ORM models, so most patches will find
   their work already done and no-op. That is the intended behaviour — the
   ``applied`` predicate makes fresh-vs-upgrade paths converge automatically.

Adding a patch
--------------
Update the ORM model first (so fresh installs get the right shape), then append a
patch describing how to bring an *existing* database to that same shape::

    Patch(
        name="users.avatar_url",
        applied=lambda insp: has_column(insp, "users", "avatar_url"),
        apply=["ALTER TABLE users ADD COLUMN avatar_url TEXT"],
    )

For destructive or multi-step work, pass a callable instead of a SQL list::

    def _rebuild(conn):
        conn.exec_driver_sql("CREATE TABLE users_new (...)")
        conn.exec_driver_sql("INSERT INTO users_new SELECT ... FROM users")
        conn.exec_driver_sql("DROP TABLE users")
        conn.exec_driver_sql("ALTER TABLE users_new RENAME TO users")

    Patch(
        name="users.drop_legacy_col",
        applied=lambda insp: not has_column(insp, "users", "legacy_col"),
        apply=_rebuild,
    )

Note the inverted predicate for a drop: "applied" means "the desired end state is
already true", so a drop patch is applied once the column is *gone*.

Launch state (2026-08-17)
-------------------------
This project has never shipped, so there is **no pre-existing database** to
upgrade. Every column the ORM models declare (conversations.summary_msg_seq,
conversations.summary_archived_count, conversations.pinned_instruction,
messages.seq, messages.content_token_count, users.timezone, ...) is already built
by ``Base.metadata.create_all`` on a fresh install. The earlier "add column"
patches that once carried an older schema forward have therefore been removed: on
a never-shipped database they would only ever no-op, and keeping them would be
dead machinery that future readers mistake for required upgrade steps.

The only remaining patch is ``tenant.normalize_to_shared`` — a data-integrity
guard rather than a schema change. It self-corrects any row whose ``tenant_id``
drifted from ``settings.default_tenant_id`` (e.g. seeded during local dev/testing),
and is a no-op the moment the data is already consistent. It stays because a
fresh database can still receive stray tenant_ids via test fixtures, and we want
the running instance to converge to the single-tenant posture automatically.
"""

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Union

from sqlalchemy import Connection, Inspector

logger = logging.getLogger("ragclaw.schema")


# ─── Inspection helpers (use these inside ``applied`` predicates) ───

def has_table(insp: Inspector, table: str) -> bool:
    """True if ``table`` exists in the database."""
    return insp.has_table(table)


def has_column(insp: Inspector, table: str, column: str) -> bool:
    """True if ``table`` exists AND has ``column``.

    Returns False for a missing table rather than raising, so a patch guarding a
    table that ``create_all`` will build anyway degrades to a no-op instead of an
    error.
    """
    if not insp.has_table(table):
        return False
    return any(c["name"] == column for c in insp.get_columns(table))


def has_index(insp: Inspector, table: str, index: str) -> bool:
    """True if ``table`` exists AND carries an index named ``index``."""
    if not insp.has_table(table):
        return False
    return any(i["name"] == index for i in insp.get_indexes(table))


def column_type(insp: Inspector, table: str, column: str) -> str | None:
    """Return the declared type of ``table.column`` as an uppercase string, or None."""
    if not insp.has_table(table):
        return None
    for c in insp.get_columns(table):
        if c["name"] == column:
            return str(c["type"]).upper()
    return None


# ─── Patch definition ───

ApplyFn = Union[Sequence[str], Callable[[Connection], None]]


@dataclass(frozen=True)
class Patch:
    """A single idempotent schema change.

    Attributes:
        name: Stable human-readable identifier, used only for logging.
        applied: Predicate over an :class:`Inspector` returning True when the
            desired end state already holds. Must inspect live schema state, not
            a stored version number.
        apply: Either a sequence of SQL statements executed in order, or a
            callable receiving the sync :class:`Connection` for multi-step work.
    """

    name: str
    applied: Callable[[Inspector], bool]
    apply: ApplyFn


def _normalize_tenant_ids(conn: Connection) -> None:
    """Fold every existing ``tenant_id`` into the shared single-tenant value.

    Pre-launch convenience normalization. Historically each new user was assigned a
    random ``tenant_id``, which scoped every tenant-owned resource (skills, MCP
    servers, KBs, documents, cron jobs, notifications) privately to that user
    instead of shared across the org. RAGClaw is deployed as ONE private instance
    where all users share a single tenant (``settings.default_tenant_id``). This
    patch rewrites every mismatched row once so those resources become visible to
    all users. Idempotent: only rows that differ are updated, so re-running is a
    no-op on an already-normalized database.
    """
    from app.config import settings

    default_tenant = settings.default_tenant_id
    tenant_tables = (
        "users",
        "skills",
        "mcp_servers",
        "knowledge_bases",
        "documents",
        "cron_jobs",
        "notifications",
    )
    for table in tenant_tables:
        if not conn.dialect.has_table(conn, table):
            continue
        conn.exec_driver_sql(
            f"UPDATE {table} SET tenant_id = :t "
            f"WHERE tenant_id IS NOT NULL AND tenant_id <> :t",
            {"t": default_tenant},
        )


def _tenant_normalized(insp: Inspector) -> bool:
    """Return True when no tenant-owned row still needs normalization (skip)."""
    from app.config import settings
    from sqlalchemy import text

    bind = insp.bind
    if bind is None or not insp.has_table("users"):
        return True
    default_tenant = settings.default_tenant_id
    tenant_tables = (
        "users",
        "skills",
        "mcp_servers",
        "knowledge_bases",
        "documents",
        "cron_jobs",
        "notifications",
    )
    for table in tenant_tables:
        if not insp.has_table(table):
            continue
        cols = {c["name"] for c in insp.get_columns(table)}
        if "tenant_id" not in cols:
            continue
        row = bind.execute(
            text(
                f"SELECT 1 FROM {table} "
                f"WHERE tenant_id IS NOT NULL AND tenant_id <> :t LIMIT 1"
            ),
            {"t": default_tenant},
        ).first()
        if row is not None:
            return False
    return True


# ─── The patch list ───
#
# Append new patches at the END. Order matters only when one patch depends on
# another's result (e.g. add a column, then backfill it). Never delete a patch.
#
# At launch this list intentionally holds only the tenant data-integrity guard.
# See the module docstring ("Launch state") for why the old add-column patches
# were dropped: with no pre-existing database, create_all already builds the
# final schema, so those patches would only ever no-op.

PATCHES: list[Patch] = [
    # Example — kept commented as a template for the next real patch.
    #
    # Patch(
    #     name="users.avatar_url",
    #     applied=lambda insp: has_column(insp, "users", "avatar_url"),
    #     apply=["ALTER TABLE users ADD COLUMN avatar_url TEXT"],
    # ),

    # Single-tenant data-integrity guard. Fold any row whose tenant_id drifted
    # from settings.default_tenant_id (e.g. seeded during local dev/testing) back
    # into the shared tenant so tenant-owned resources (skills, MCP servers, KBs,
    # documents, cron jobs, notifications, users) are visible to all users.
    # Idempotent on data: applied() skips once no mismatched row remains, so on a
    # freshly-launched database this is a no-op.
    Patch(
        name="tenant.normalize_to_shared",
        applied=_tenant_normalized,
        apply=_normalize_tenant_ids,
    ),

    # Per-KB retrieval configuration columns. Added 2026-08-18.
    # These allow each knowledge base to override global retrieval defaults.
    Patch(
        name="knowledge_bases.vector_weight",
        applied=lambda insp: has_column(insp, "knowledge_bases", "vector_weight"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN vector_weight FLOAT"],
    ),
    Patch(
        name="knowledge_bases.bm25_weight",
        applied=lambda insp: has_column(insp, "knowledge_bases", "bm25_weight"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN bm25_weight FLOAT"],
    ),
    Patch(
        name="knowledge_bases.vector_top_k",
        applied=lambda insp: has_column(insp, "knowledge_bases", "vector_top_k"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN vector_top_k INTEGER"],
    ),
    Patch(
        name="knowledge_bases.bm25_top_k",
        applied=lambda insp: has_column(insp, "knowledge_bases", "bm25_top_k"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN bm25_top_k INTEGER"],
    ),
    Patch(
        name="knowledge_bases.final_top_k",
        applied=lambda insp: has_column(insp, "knowledge_bases", "final_top_k"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN final_top_k INTEGER"],
    ),
    Patch(
        name="knowledge_bases.similarity_threshold",
        applied=lambda insp: has_column(insp, "knowledge_bases", "similarity_threshold"),
        apply=["ALTER TABLE knowledge_bases ADD COLUMN similarity_threshold FLOAT"],
    ),
]


def run_patches(conn: Connection) -> None:
    """Apply every patch whose desired end state is not yet reached.

    Runs on a *sync* connection (call via ``conn.run_sync``). Each patch is
    checked against live schema state and skipped when already satisfied, so this
    is safe to run on every startup and from every stack concurrently.
    """
    from sqlalchemy import inspect as sa_inspect

    applied_count = 0
    for patch in PATCHES:
        # Re-inspect per patch: an earlier patch may have changed the schema, and
        # Inspector caches its reflection results.
        insp = sa_inspect(conn)
        try:
            if patch.applied(insp):
                continue
        except Exception:
            logger.exception("[schema] patch %s: guard check failed, skipping", patch.name)
            continue

        try:
            if callable(patch.apply):
                patch.apply(conn)
            else:
                for stmt in patch.apply:
                    conn.exec_driver_sql(stmt)
            applied_count += 1
            logger.info("[schema] patch applied: %s", patch.name)
        except Exception:
            # Do not abort startup for the remaining patches — one failing patch
            # should not block unrelated ones. Drift detection reports the
            # resulting inconsistency loudly right after.
            logger.exception("[schema] patch FAILED: %s", patch.name)

    if applied_count == 0:
        logger.debug("[schema] no patches needed (%d checked)", len(PATCHES))


# ─── Drift detection ───

def detect_drift(conn: Connection, metadata) -> list[str]:
    """Compare ORM models against the live database and return a list of problems.

    Catches the one real failure mode of this scheme: a model gained a column but
    nobody wrote the matching patch, which would otherwise surface much later as a
    runtime ``no such column`` error on some unrelated request.

    Detects missing tables and missing columns. Deliberately does NOT flag extra
    tables/columns present in the database but absent from the models — those are
    legitimate during a rolling multi-stack change, where one stack has already
    added a column that another stack's models do not know about yet.

    Returns:
        A list of human-readable problem descriptions; empty when the database
        matches the models.
    """
    from sqlalchemy import inspect as sa_inspect

    insp = sa_inspect(conn)
    problems: list[str] = []

    live_tables = set(insp.get_table_names())
    for table_name, table in metadata.tables.items():
        if table_name not in live_tables:
            problems.append(f"missing table: {table_name}")
            continue
        live_cols = {c["name"] for c in insp.get_columns(table_name)}
        for col in table.columns:
            if col.name not in live_cols:
                problems.append(f"missing column: {table_name}.{col.name}")

    return problems
