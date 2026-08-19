# Database

The ORM models in `backend/app/models/` are the schema source of truth. There are
no migration files and no revision chain. Every startup runs three idempotent
stages:

1. **`create_all(checkfirst=True)`** — creates whole missing tables from the models.
   It never modifies a table that already exists.
2. **Idempotent patches** (`backend/app/schema_patches.py`) — everything step 1
   cannot do: add/drop a column, change a type, rebuild an index, backfill data.
   Each patch guards itself on *live schema state*, so it is safe to re-run forever
   and there is no ordering constraint between stacks.
3. **Drift detection** — compares the models against the live database and logs an
   error listing anything still missing, so a model changed without a matching
   patch is caught at startup instead of failing mid-request later.

## Fresh install

Delete `data/sqlite/ragclaw.db` and start the backend — the DB and seed data rebuild automatically.

## Evolve schema

**Adding a whole new table** — define the model, restart. Done; `create_all` picks
it up.

**Any change to an existing table** (add/drop column, change type, add index,
backfill) — edit the model *and* append a patch to `backend/app/schema_patches.py`:

```python
Patch(
    name="users.avatar_url",
    applied=lambda insp: has_column(insp, "users", "avatar_url"),
    apply=["ALTER TABLE users ADD COLUMN avatar_url TEXT"],
)
```

The model change makes fresh installs correct; the patch brings existing databases
to the same shape. Forget the patch and startup logs a loud `DRIFT DETECTED` error
naming the missing column.

Rules: guard on **live schema state**, never on a version number — a sibling stack
may have applied a different subset. Patches are **append-only**; deleting one
breaks any database that has not applied it yet. For a drop, invert the predicate
(`applied` means "desired end state reached", so a drop is applied once the column
is gone). Pass a callable instead of a SQL list for multi-step work such as
SQLite's table-rebuild dance.

Multi-stack is safe by construction: every stack runs the same self-guarding
patches in any order, with no shared version counter to conflict over.