# Sprint 6A.0 — Dissertation Workspace Foundation

## Status: Proposed (architecture, not yet implemented)

## Objective

Establish the local, deterministic data model for managing a dissertation as
structured work: chapters, subchapters, document versions, paragraph maps,
review jobs, and revision logs — persisted locally, with a service-layer API
other sprints can build on. This sprint is data-model-and-service-layer
**only**: no Telegram surface, no Google Drive access, no DOCX parsing, no
citation handling, no model-generated content.

## Scope

1. **Domain models**: `Chapter`, `Subchapter`, `DocumentVersion`,
   `ParagraphMap`, `ReviewJob`, `RevisionLogEntry` — mirroring the existing
   Workspace Memory pattern (`Project`/`Task`/`Note`/`Decision`/`Session`)
   in shape and rigor.
2. **Chapter/subchapter registry**: ordered hierarchy, status lifecycle
   (`draft`, `in_review`, `revised`, `final`), CRUD via the service layer.
3. **Document versions**: an append-only version history per
   chapter/subchapter (content-hash-addressed, not raw-content-in-DB — see
   below), so future sprints (DOCX import, model revision) have a place to
   record "what changed and when" without this sprint needing to know how
   those future sprints produce content.
4. **Paragraph maps**: a structural index (paragraph ordinal → stable ID →
   current version reference) that lets a future sprint address "paragraph 7
   of chapter 3" durably even as content changes — the map itself, not
   paragraph *text*, is what this sprint owns.
5. **Review jobs**: a request/status record (`queued`, `in_progress`,
   `completed`, `failed`) for "a review of X happened/is happening" — this
   sprint defines the record and lifecycle only; it does not implement what
   a review actually *does* (no model call, no content analysis).
6. **Revision logs**: an append-only audit trail of who/what changed which
   chapter/subchapter/version and why (free-text reason, sensitive-content
   guarded like every other free-text field in NOVA).

## Out of scope

- Google Drive access of any kind (depends on Sprint 5C's `get_google_client`
  once that lands, but no Drive *calls* here).
- DOCX/PDF/any document-format parsing or generation.
- Citation management (BibTeX, Zotero, etc.).
- Model-generated revisions or reviews (no `app.providers`/`app.execution`
  dependency in this sprint — a future sprint wires review jobs to an actual
  model call).
- Telegram commands (deferred to a follow-up sprint, e.g. 6A.1) — this keeps
  6A.0's file footprint minimal and entirely outside `app/telegram_bot.py`.

## Architecture

```text
(no Telegram entry point in this sprint)

app/dissertation/
    service.py       ← DissertationService: validated use cases, mirrors
                        app/memory/services.py's shape
        │
        ├── repository.py    ← parameterized SQL only
        │       │
        │       └── MemoryDatabase (existing, shared connection)
        │
        ├── models.py         ← Chapter, Subchapter, DocumentVersion,
        │                        ParagraphMap, ReviewJob, RevisionLogEntry
        └── schema.py          ← additive CREATE TABLE IF NOT EXISTS
```

`app/dissertation/` depends only on `app.memory.database.MemoryDatabase` and
`app.security.SENSITIVE_CONTENT_PATTERN` — the same minimal dependency
footprint as `app/execution/` had at its own foundation stage. It does not
depend on `app.google`, `app.providers`, `app.execution`, or `app.router`.

## Modules and file paths

**New files only:**

```text
app/dissertation/__init__.py
app/dissertation/service.py
app/dissertation/repository.py
app/dissertation/models.py
app/dissertation/schema.py
tests/test_dissertation_schema.py
tests/test_dissertation_repository.py
tests/test_dissertation_service.py
docs/dissertation-workspace.md
```

**Existing files, additive changes:**

```text
app/main.py   # construct DissertationService alongside existing services;
              # call its initialize() — one small, isolated addition
```

`app/config.py` and `app/telegram_bot.py` are **not touched** by this
sprint — the dissertation domain reuses the existing
`NOVA_MEMORY_DB_PATH`-configured database with no new settings needed.

## Interfaces exposed to other sprints

```python
# app/dissertation/service.py — the stable surface for a future Telegram
# sprint (6A.1) and future content sprints (Drive import, model review):
class DissertationService:
    def create_chapter(self, title: str, order: int) -> Chapter: ...
    def create_subchapter(self, chapter_id: int, title: str, order: int) -> Subchapter: ...
    def record_document_version(
        self, target_type: str, target_id: int, content_hash: str, source: str
    ) -> DocumentVersion: ...
    def build_paragraph_map(self, version_id: int, paragraph_count: int) -> list[ParagraphMap]: ...
    def create_review_job(self, target_type: str, target_id: int) -> ReviewJob: ...
    def update_review_job_status(self, job_id: int, status: str, summary: str = "") -> ReviewJob: ...
    def append_revision_log(
        self, target_type: str, target_id: int, actor: str, reason: str
    ) -> RevisionLogEntry: ...
```

`record_document_version` takes a `content_hash`, not raw document content —
this sprint's schema never stores full document text (see Security). A
future sprint that actually imports/generates content is responsible for its
own content storage and passes only a hash + `source` label here.

## Database/schema changes

Six new tables, additive, matching the established idempotent pattern:

```sql
CREATE TABLE IF NOT EXISTS dissertation_chapters (
    id INTEGER PRIMARY KEY, title TEXT NOT NULL, order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_review','revised','final')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dissertation_subchapters (
    id INTEGER PRIMARY KEY,
    chapter_id INTEGER NOT NULL REFERENCES dissertation_chapters(id) ON DELETE CASCADE,
    title TEXT NOT NULL, order_index INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','in_review','revised','final')),
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dissertation_document_versions (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter','subchapter')),
    target_id INTEGER NOT NULL,
    content_hash TEXT NOT NULL,      -- SHA-256; never raw content
    source TEXT NOT NULL,             -- free label, e.g. 'manual', future: 'drive_import'
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dissertation_paragraph_maps (
    id INTEGER PRIMARY KEY,
    version_id INTEGER NOT NULL REFERENCES dissertation_document_versions(id) ON DELETE CASCADE,
    paragraph_ordinal INTEGER NOT NULL,
    stable_paragraph_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(version_id, paragraph_ordinal)
);
CREATE TABLE IF NOT EXISTS dissertation_review_jobs (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter','subchapter')),
    target_id INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
        CHECK (status IN ('queued','in_progress','completed','failed')),
    summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dissertation_revision_log (
    id INTEGER PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('chapter','subchapter')),
    target_id INTEGER NOT NULL,
    actor TEXT NOT NULL,             -- 'user:<telegram_id>' | 'system'
    reason TEXT NOT NULL,             -- sensitive-content guarded
    created_at TEXT NOT NULL
);
```

All tables are created via `CREATE TABLE IF NOT EXISTS` appended to their own
`app/dissertation/schema.py` `SCHEMA` string, applied by
`DissertationService.initialize()` — identical mechanism to
`app/execution/schema.py` and `app/providers/schema.py`. No existing table is
altered.

## Security constraints

- **No raw document/paragraph text is ever persisted by this sprint** —
  only content hashes and structural metadata. This is a deliberate scope
  boundary: dissertation content is the user's academic work product, and
  this foundation sprint should not become an unreviewed place for full
  document text to land in a local SQLite file before storage/retention
  policy for that content is explicitly designed.
- `reason` (revision log) and `summary` (review job) free-text fields are
  passed through `SENSITIVE_CONTENT_PATTERN` (from `app.security`) before
  storage, exactly like `app/memory/services.py`'s note/decision/session
  fields.
- No shell, subprocess, filesystem write outside the SQLite DB, network
  call, or Git action exists anywhere in this sprint's code.
- No dependency on `app.google` or `app.providers` — this sprint cannot leak
  dissertation metadata to any external service because it has no code path
  capable of making an external call at all.

## Backward-compatibility requirements

- Purely additive tables; no existing table or column touched.
- No existing command, service, or test is affected — `app/dissertation/` is
  inert with respect to every currently-shipped feature until a future
  sprint wires a Telegram command to it.
- All 273 existing tests remain green, unmodified.

## Tests

- `test_dissertation_schema.py`: idempotent `CREATE TABLE IF NOT EXISTS`
  initialization; running `initialize()` twice doesn't error or duplicate
  rows; foreign-key cascade from chapter deletion to subchapters/versions.
- `test_dissertation_repository.py`: CRUD for each of the six tables;
  `CHECK` constraints reject invalid `status`/`target_type` values; unique
  constraint on `(version_id, paragraph_ordinal)` enforced.
- `test_dissertation_service.py`: chapter/subchapter ordering; recording a
  document version never accepts anything but a hash-shaped string (reject
  obviously-raw-text input as a defensive check, even though the type is
  just `str`); paragraph map build is deterministic for a given
  `paragraph_count`; review job status transitions follow
  `queued → in_progress → {completed, failed}` only, invalid transitions
  rejected; revision log rejects sensitive-content `reason` text the same
  way Workspace Memory rejects sensitive notes.
- Regression: full existing 273-test suite unaffected.

## Acceptance criteria

- [ ] Six new tables created idempotently; no existing table/column altered.
- [ ] `DissertationService` covers create/list for chapters and subchapters,
      version recording, paragraph map construction, review job lifecycle,
      and revision logging.
- [ ] No raw document or paragraph text is ever written to the database —
      verified by a test asserting only hash-shaped values are accepted/
      stored in `content_hash`.
- [ ] Zero Telegram, Google, provider, or execution dependency in this
      sprint's code.
- [ ] All 273 existing tests plus all new Sprint 6A.0 tests pass.

## Documentation deliverables

- `docs/SPRINT_6A0.md` (this file).
- `docs/dissertation-workspace.md`: entity/relationship description (mirroring
  `docs/workspace-memory.md`'s style), explicitly noting this is a foundation
  layer with no Telegram surface yet, and what a follow-up sprint (6A.1) is
  expected to add (Telegram commands) versus later sprints (Drive import,
  model-assisted review).
- `docs/CURRENT_SPRINT.md` updated with the Sprint 6A.0 entry.

## File ownership boundary

Sprint 6A.0 owns `app/dissertation/` exclusively. Its only shared-file touch
is one small, additive block in `app/main.py` (construct + initialize
`DissertationService`). It does not touch `app/config.py` or
`app/telegram_bot.py` at all.
