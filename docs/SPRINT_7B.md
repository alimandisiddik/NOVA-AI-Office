# Sprint 7B — Knowledge Operations

## Status: **FROZEN** — final ChatGPT/Control Tower architecture decisions applied.

See `docs/WAVE_6_SHARED_CONTRACTS.md` §2.6 and AD-W6-04 for the contract
this spec implements.

## 1. Objective

Provide a provenance-first local knowledge capability: KNOWLEDGE ITEM →
SOURCE/PROVENANCE → INDEX/METADATA → QUERY → RESULT WITH PROVENANCE. Metadata
and provenance first, not sophisticated RAG.

## 2. User-visible usable capability

The authorized Telegram user can record a knowledge source and a knowledge
item derived from it (manually, via a structured command — no autonomous
ingestion), then query stored knowledge items by keyword/tag and get results
that always show their originating source and citation.

## 3. Scope

- New `app/knowledge/` package: `models.py`, `schema.py`, `repository.py`,
  `service.py`.
- `KnowledgeSource` and `KnowledgeItem` per
  `docs/WAVE_6_SHARED_CONTRACTS.md` §2.6.
- `KnowledgeService.create_source(...)`, `create_item(...)`,
  `query(keyword: str | None = None, tag: str | None = None, project_id:
  int | None = None) -> list[KnowledgeResult]` where `KnowledgeResult` pairs
  a `KnowledgeItem` with its `KnowledgeSource` (provenance always attached,
  never a bare item).
- New Telegram commands, mirroring Dissertation's `/dissertation
  addsource`/`addevidence` pipe-delimited grammar exactly (no conversational
  parsing):
  - `/knowledgesource <title> | <source type> | <citation> [| <locator>]`
  - `/knowledgeitem <source id> | <title> | <summary> [| <tags>] [| <confidence>]`
  - `/knowledgequery <keyword or tag>`
- Documented (not implemented) Gemini/Drive ingestion seam: §2.6's
  `register_source_from_drive(...)` signature, accepting
  `app.google_workspace.drive.models.DriveFileMetadata` — metadata only,
  no file content, no autonomous call site anywhere in this sprint.

## 4. Out of scope

- Large vector infrastructure / embeddings / semantic search.
- Autonomous web crawling or Google Scholar scraping.
- Arbitrary external ingestion of any kind.
- Automatic factual rewriting of stored knowledge.
- Destructive file organization.
- Any actual Google Workspace read/write call (the Gemini boundary is a
  documented interface, not a working integration, this sprint).

## 5. Existing architecture reused

- Provenance/confidence pattern from `app.dissertation.models.Source`/
  `Evidence` (field shapes and the `LOW`/`MEDIUM`/`HIGH` confidence enum are
  copied deliberately, not reinvented) — but the tables and package are new
  and separate (AD-W6-04); Dissertation's own tables are untouched.
- `app.security.SENSITIVE_CONTENT_PATTERN` for all new free text.
- Pipe-delimited Telegram command grammar (`/task`, `/note`, `/dissertation
  addsource`) as the precedent for the three new commands — no new parsing
  style introduced.
- `app.google_workspace.drive.models.DriveFileMetadata` — referenced by type
  only for the future ingestion seam's signature; no import of
  `app.google_workspace.drive.service` and no live call.
- Optional loose links to `app.memory.models.Project` and
  `app.control_tower.models.WorkItem` — read-only existence checks only
  (mirrors `ControlTowerRepository.project_exists()`), no write access to
  either domain.

## 6. Owned files/modules

- `app/knowledge/{models,schema,repository,service}.py` — new, 7B-exclusive.
- `app/telegram_bot.py` — one additive block (three new commands).
- `tests/test_knowledge_*.py` — new, 7B-exclusive.

## 7. Shared dependencies

- `app/memory/**` — read only (optional `project_id` existence check).
- `app/control_tower/**` — read only (optional `work_item_id` existence
  check via `ControlTowerService.repository.get_work_item()` or an
  equivalent public read method — no write call).
- `app/google_workspace/drive/models.py` — type reference only, for the
  documented future ingestion seam's signature.
- `app/main.py`, `app/telegram_bot.py` — shared, ordered append.

## 8. Data/contracts

New additive tables, owned entirely by `app/knowledge/schema.py`:

```sql
CREATE TABLE IF NOT EXISTS knowledge_sources (
    id            INTEGER PRIMARY KEY,
    title         TEXT NOT NULL,
    source_type   TEXT NOT NULL
                  CHECK (source_type IN
                      ('document','note','drive_file','calendar_event','manual','conversation')),
    origin_system TEXT NOT NULL DEFAULT 'manual'
                  CHECK (origin_system IN
                      ('manual','google_drive','google_calendar','dissertation','telegram')),
    origin_ref    TEXT,
    citation_text TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','archived')),
    created_at    TEXT NOT NULL,
    updated_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS knowledge_items (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER NOT NULL REFERENCES knowledge_sources(id) ON DELETE CASCADE,
    project_id   INTEGER,
    work_item_id TEXT,
    title        TEXT NOT NULL,
    summary      TEXT NOT NULL,
    tags         TEXT NOT NULL DEFAULT '',
    confidence   TEXT NOT NULL DEFAULT 'MEDIUM' CHECK (confidence IN ('LOW','MEDIUM','HIGH')),
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_source ON knowledge_items(source_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_items_tags ON knowledge_items(tags);
CREATE TABLE IF NOT EXISTS knowledge_audit_log (
    id         INTEGER PRIMARY KEY,
    operation  TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id  TEXT NOT NULL,
    actor      TEXT NOT NULL,
    detail     TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
```

`project_id`/`work_item_id` are deliberately **not** foreign keys —
cross-domain loose references, same discipline as
`DispatchRequest.source_id` (Wave 3 §3): knowledge must not hard-depend on
Control Tower's or Workspace Memory's schema.

## 9. Security constraints

- `citation_text`, `summary`, `title`, `tags` all screened with
  `SENSITIVE_CONTENT_PATTERN` before any write, matching Dissertation's
  `create_evidence` discipline exactly.
- `origin_ref` is an opaque reference (e.g. a Drive `file_id`) — never a raw
  URL with embedded credentials, never file content.
- No network call anywhere in this sprint's code path — `query()` is pure
  local SQLite `LIKE`/exact-match filtering.
- Telegram errors are sanitized (no SQL, stack trace, or path leakage),
  matching every existing domain's error-handling convention.

## 10. Tests

- `KnowledgeService.create_source`/`create_item` — valid input, sensitive
  content rejection, length bounds, invalid `source_type`/`confidence`
  enum values.
- `query()` — by keyword, by tag, by project, empty result, provenance
  always present on every result row.
- Loose-reference validation — `work_item_id` referencing a real vs.
  nonexistent `WorkItem` (existence check only, not a hard FK failure mode).
- Telegram command parsing for all three new commands: valid, missing
  required field, malformed pipe count.
- Additive schema migration test (mirrors
  `tests/test_dissertation_schema.py`'s pattern): `apply_schema()` is
  idempotent against a fresh DB and a DB that already has the tables.

## 11. Acceptance criteria

1. `/knowledgesource` then `/knowledgeitem` then `/knowledgequery` round-trip
   end to end via Telegram, returning a result with its source's citation
   attached.
2. No knowledge item can exist without a valid `source_id`.
3. No sensitive-content-pattern text is ever persisted.
4. Zero edits to `app/dissertation/**` or any other existing package.
5. Full existing regression suite passes unmodified.

## 12. Integration contract

Wave 1 (parallel with 7A, 7D — no code dependency on either). Independently
mergeable at any point once its own gates pass (`docs/WAVE_6_SHARED_CONTRACTS.md`
§5).

## 13. Explicit prohibited edits

- No edits to `app/dissertation/**` (the nearest existing pattern, reused
  conceptually only — see AD-W6-04).
- No edits to `app/google_workspace/**` (type reference only).
- No edits to `app/control_tower/**` or `app/memory/**` beyond read calls.
- No edits to another sprint's block in `app/main.py`/`app/telegram_bot.py`.

## 14. Known risks / technical debt

- `query()`'s `LIKE`-based matching is intentionally unsophisticated (no
  ranking, no fuzzy match, no embeddings) — acceptable for v1 per the
  brief's explicit "safe querying, not sophisticated RAG" instruction; a
  future sprint may add ranking without changing the underlying schema.
- The Gemini/Drive ingestion seam is a documented signature only; if a
  future sprint implements it, `origin_ref` validation (path/ID shape) will
  need the same rigor `app/google_workspace/drive/` already applies to
  `PathSecurityError`/`SizeLimitError` — not built here, flagged so it isn't
  forgotten.
