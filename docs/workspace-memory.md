# Workspace Memory

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
/note <project name> | <note content>
/decision <project name> | <decision> [| <reason>]
/resume <project name>
/progress <project name>
/continue <project name>
```

Examples:

```text
/project NOVA AI Office | Executive multi-agent office
/task NOVA AI Office | Add SQLite test coverage | high
/note NOVA AI Office | Workspace Memory uses local SQLite only.
/decision NOVA AI Office | Keep Sprint 2 local-first | Cloud services are out of scope.
/resume NOVA AI Office
```

`/resume` returns project status, task progress, active tasks, recent notes and decisions, and the latest session. `/progress` calculates completion as `done / all non-cancelled tasks`, returning `0%` when no applicable tasks exist. `/continue` returns the latest session, unfinished tasks, recent decisions, and a recommended next action.

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
- Sprint 2 records work sessions through the service layer; there is no dedicated Telegram session-creation command yet.
- The text fields are intentionally plain text and do not inspect or redact content entered by the user.
