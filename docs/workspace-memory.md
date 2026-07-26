# Workspace Memory

## Sprint 2.1 additions

Sprint 2.1 adds Telegram-accessible task lifecycle management and work-session recording. The existing SQLite schema already contains `tasks.completed_at` and all required `sessions` fields, so no migration, table drop, or database reset is required. Existing Sprint 2 databases remain usable.

## Overview

Sprint 2 adds a local SQLite Workspace Memory system to NOVA. It enables the authorized Telegram user to save work context and resume a project without reconstructing the prior state manually.

## Database lifecycle

`NOVA_MEMORY_DB_PATH` configures the database location. Its default value is `data/nova_memory.db`, relative to the repository root. NOVA creates the parent directory and initializes the schema when `python -m app.main` starts.

The database uses SQLite with foreign keys enabled for every connection. All system-managed timestamps are stored in UTC as ISO 8601 values ending in `Z`.

## Entities and relationships

| Entity | Purpose | Relationship |
| --- | --- | --- |
| Project | Top-level work context with a lifecycle status. | Has many tasks, notes, decisions, and sessions. |
| Task | A unit of work with status and priority. | Belongs to one project. |
| Note | Plain-text project context. | Belongs to one project. |
| Decision | Immutable decision with an optional reason. | Belongs to one project. |
| Session | Work summary, completed items, and next action. | Belongs to one project. |

Project names are unique case-insensitively. Valid project statuses are `active`, `paused`, `completed`, and `archived`. Task statuses are `todo`, `doing`, `done`, and `cancelled`; valid priorities are `low`, `normal`, `high`, and `urgent`.

The active project is the most recently created project with status `active`. Current Telegram commands primarily require an explicit project name.

## Command grammar

Pipe-separated fields are trimmed. Required fields cannot be empty.

```text
/project <project name> [| <description>]
/projects
/task <project name> | <task title> [| <priority>]
/tasks <project name> [| <status>]
/task_status <project name> | <task ID or exact title> | <todo|doing|done|cancelled>
/note <project name> | <note content>
/decision <project name> | <decision> [| <reason>]
/session <project name> | <summary> [| <completed items> [| <next action>]]
/sessions <project name> [| <limit 1-10>]
/resume <project name>
/progress <project name>
/continue <project name>
```

Examples:

```text
/project NOVA AI Office | Executive multi-agent office
/task NOVA AI Office | Add SQLite test coverage | high
/task_status NOVA AI Office | 3 | done
/note NOVA AI Office | Workspace Memory uses local SQLite only.
/decision NOVA AI Office | Keep Sprint 2 local-first | Cloud services are out of scope.
/session NOVA AI Office | Sprint 2 completed | Telegram and unit tests passed | Prepare Sprint 2.1
/sessions NOVA AI Office | 5
/resume NOVA AI Office
```

`/tasks` displays task IDs. `/task_status` accepts an exact title case-insensitively or a numeric task ID. If an exact title has multiple matches, NOVA lists matching IDs and makes no change until the user retries with an ID.

Supported task statuses are `todo`, `doing`, `done`, and `cancelled`. When a task becomes `done`, NOVA sets `completed_at` in UTC if it is empty. When a task moves away from `done`, NOVA clears it. A no-op status request returns a clear message.

`/session` requires a project and summary; completed items and next action are optional. Telegram-created sessions use current UTC time for `started_at`, `ended_at`, and `created_at`. `/sessions` defaults to five newest-first items and accepts a limit from 1 to 10.

`/resume` returns project status, task progress, doing/todo work, recently completed tasks, notes, decisions, and the latest session context. `/progress` calculates completion as `done / all non-cancelled tasks`, returning `0%` when no applicable tasks exist. `/continue` recommends work in this order: latest session next action, a priority-ranked `doing` task, a priority-ranked `todo` task, or a no-pending-action message.

## Backup and restore

Back up the SQLite database only while NOVA is stopped, or use SQLite's backup mechanism. Store backups in a secure local location. To restore, stop NOVA, replace the configured database file with the backup, then restart NOVA.

NOVA does not synchronize Workspace Memory to Google Drive or another cloud service in Sprint 2.

## Reset

To reset Workspace Memory, stop NOVA and delete the configured database file. Also remove any adjacent SQLite `-wal` and `-shm` files when they exist. This permanently removes local projects, tasks, notes, decisions, and sessions unless you have a backup.

## Privacy and security

- The SQLite database is ignored by Git.
- Telegram authorization continues to use `TELEGRAM_ALLOWED_USER_ID`.
- Never store bot tokens, credentials, API keys, passwords, or `.env` values in Workspace Memory text fields.
- Errors shown to Telegram users do not expose SQL statements, stack traces, paths, tokens, or environment values.
- Local SQLite storage is not an encryption-at-rest solution; protect the Mac user account and local backups.

## Known limitations

- Workspace Memory is local to one machine and one database file.
- There is no cloud synchronization, multi-user collaboration, or external tool integration.
- Session recording is append-only; there is no edit or delete workflow in Sprint 2.1.
- Task due dates are stored in the schema but are not yet exposed through Telegram commands.
