# Knowledge Operations

Sprint 7B adds a local, provenance-first knowledge register. It stores short
metadata records in the existing local SQLite database; it does not crawl,
fetch external content, embed documents, or call Google services.

## Commands

- `/knowledgesource <title> | <source type> | <citation> [| <locator>]`
- `/knowledgeitem <source id> | <title> | <summary> [| <tags>] [| <confidence>]`
- `/knowledgequery <keyword or tag>`

`locator` is stored as an opaque `origin_ref`; it is never fetched. Telegram
manual entries are recorded with `origin_system=telegram`. Valid source types
are `document`, `note`, `drive_file`, `calendar_event`, `manual`, and
`conversation`. Confidence is `LOW`, `MEDIUM`, or `HIGH` and defaults to
`MEDIUM`.

Every query result joins the knowledge item to its `KnowledgeSource`, so title,
source type, and citation are always returned together. Optional `project_id`
and `work_item_id` service arguments are existence-checked through read-only
Workspace Memory and Control Tower seams; they remain loose SQLite references.

## Future boundary

`KnowledgeService` documents, but does not implement,
`register_source_from_drive(metadata: DriveFileMetadata, actor: str)`. A future
implementation may consume metadata only and must not introduce autonomous
ingestion or file-content reads.

## Acceptance-Criteria Matrix

| Criterion | Evidence |
| --- | --- |
| Telegram round-trip returns citation provenance | `tests/test_telegram_knowledge.py` |
| Items require a valid source | `tests/test_knowledge_service.py` |
| Sensitive text is rejected before persistence | `tests/test_knowledge_security.py` |
| Existing domain packages remain unchanged | New isolated `app/knowledge/` package; only additive wiring |
| Schema is additive and idempotent | `tests/test_knowledge_schema.py` |
