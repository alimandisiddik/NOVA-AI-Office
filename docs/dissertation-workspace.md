# Dissertation Workspace

## Overview

Sprint 6A.0 provides a local, deterministic foundation for dissertation work
metadata. It uses the existing `NOVA_MEMORY_DB_PATH` SQLite database and has
no Telegram commands, Google Drive calls, document parsing, citation-manager
integration, AI review, or document transformation.

## Entities and relationships

| Entity | Purpose | Relationship |
| --- | --- | --- |
| Chapter | Ordered top-level workspace record. | Has many subchapters. |
| Subchapter | Ordered record within a chapter. | Belongs to one chapter. |
| DocumentVersion | Append-only SHA-256 metadata record. | Targets one chapter or subchapter. |
| ParagraphMap | Stable structural address for a version ordinal. | Belongs to one document version. |
| ReviewJob | Lifecycle record for a future review request. | Targets one chapter or subchapter. |
| RevisionLogEntry | Append-only reason and actor audit record. | Targets one chapter or subchapter. |

Chapter and subchapter statuses are `draft`, `in_review`, `revised`, and
`final`. Review jobs start as `queued` and may transition only through
`queued → in_progress → completed|failed`.

## Version and paragraph behavior

Document versions are append-only. They store a validated SHA-256 digest, a
source label, and metadata state, never document or paragraph text. A version
starts as `original` and progresses only through `working`, `reviewed`, and
`approved`; these state changes update metadata only, never document content.
Paragraph maps record only a
version ID, ordinal, and deterministic stable UUID. Rebuilding a map with the
same count returns its existing entries; a different count is rejected rather
than overwriting structural metadata.

Deleting a chapter cascades to subchapters and cleans up associated document
versions and paragraph maps. Direct subchapter deletion likewise cleans up its
version metadata and maps.

## Privacy and security

- No raw academic content is persisted by this sprint.
- Review summaries and revision reasons are screened with the shared
  `SENSITIVE_CONTENT_PATTERN` before storage.
- The domain has no dependency on Telegram, Google, providers, execution,
  network clients, subprocesses, or document-format tooling.
- The local database is not encryption at rest; protect the machine account
  and backups.

## Follow-up work

Sprint 6A.1 can add a Telegram interface over `DissertationService`. Later
sprints can record Drive-import metadata, retain documents under an approved
storage policy, or attach actual review execution to `ReviewJob` records.
