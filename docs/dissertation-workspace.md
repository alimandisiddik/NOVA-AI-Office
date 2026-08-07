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

## Sprint 6A full workspace

Sprint 6A adds a singleton `DissertationWorkspace` root plus linked source,
evidence, research-note, gap, Control Tower work-item, and Control Tower
decision records. Sources retain metadata and citation text only; evidence is
always sourced and bounded to a short finding, while research notes must link
to at least one academic object.

Workspace and chapter focus are manually set narrative fields. Overall
progress is computed from the simple mean of chapter-level progress and is
never stored. The next action is also computed: active academic work items,
then pending academic work items, then an in-progress gap with a next action,
then an open gap, and finally drafting/review guidance.

Research tasks and decisions are not duplicated. `DissertationService` creates
Control Tower `academic` work items and Control Tower decisions, persisting only
local linkage metadata. The Telegram `/dissertation` command is read-only and
provides overview, chapter, gaps, next-action, task, evidence, source, and
decision views. Write commands remain deferred.

Progress uses the fixed status weights `draft=0`, `in_review=50`,
`revised=75`, and `final=100`. A chapter uses its subchapter mean when it has
subchapters; otherwise it uses its own status weight. The workspace uses the
unweighted mean of chapter progress, or `0` when there are no chapters.

Next action uses this ordered cascade: critical open gap, pending linked
academic Control Tower item, any open gap, the first non-final chapter, all
chapters final, then no chapters defined. Gaps and tasks remain separate
signals and are not folded into progress.
