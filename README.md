# NOVA AI Office

NOVA AI Office is the project foundation for an executive multi-agent AI office: a governed workspace where specialized agents, services, workflows, prompts, and institutional knowledge can be introduced deliberately over time.

## Sprint 2.1 status

NOVA now includes a local-only Telegram bot and SQLite-backed Workspace Memory for the configured owner. Sprint 2.1 adds practical task-status updates and Telegram-accessible work-session recording. NOVA intentionally includes no cloud services, AI provider SDKs, credentials in source control, or business automation.

## Repository layout

```text
agents/       Agent specifications and future implementations
app/memory/   SQLite database, repositories, services, and Telegram formatters
docs/         Product, architecture, security, and sprint documentation
knowledge/    Curated knowledge assets and governance notes
prompts/      Versioned prompt assets
scripts/      Local development and maintenance scripts
services/     Service boundaries and future application services
templates/    Reusable structured templates
tests/        Automated test suite
workflows/    Workflow definitions and orchestration blueprints
```

## Local setup

1. Use Python 3.11 or later.
2. Copy `.env.example` to `.env` when local configuration is needed.
3. Create and activate a virtual environment appropriate for your platform.
4. Install the declared dependencies into the virtual environment.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Set `TELEGRAM_BOT_TOKEN` and `TELEGRAM_ALLOWED_USER_ID` in the local `.env` file. Never commit that file.

Run NOVA locally with:

```bash
scripts/run_local.sh
```

## Workspace Memory

The local SQLite database defaults to `data/nova_memory.db`; set `NOVA_MEMORY_DB_PATH` in `.env` to use another local location. NOVA creates its directory and schema automatically at startup.

Workspace Memory provides structured local context for:

- Projects and their status.
- Tasks and progress calculations.
- Plain-text notes.
- Immutable decisions with an optional reason.
- Work sessions with completed items and a recommended next action.

The active project is the most recently created project whose status is `active`. Telegram commands continue to accept an explicit project name so memory remains predictable.

See [Workspace Memory](docs/workspace-memory.md) for the architecture, command grammar, privacy boundaries, backup, restore, and reset guidance.

## Telegram commands

| Command | Purpose | Example |
| --- | --- | --- |
| `/project` | Create a project | `/project NOVA AI Office | Executive multi-agent office` |
| `/projects` | List projects | `/projects` |
| `/task` | Create a task | `/task NOVA AI Office | Build Workspace Memory | high` |
| `/tasks` | List project tasks | `/tasks NOVA AI Office | doing` |
| `/task_status` | Change a task status by ID or exact title | `/task_status NOVA AI Office | 3 | done` |
| `/note` | Store a note | `/note NOVA AI Office | SQLite selected for local memory.` |
| `/decision` | Store a decision | `/decision NOVA AI Office | Use SQLite | Local-first scope` |
| `/session` | Record a completed work session | `/session NOVA AI Office | Tests passed | 19 tests | Review changes` |
| `/sessions` | List recent work sessions | `/sessions NOVA AI Office | 5` |
| `/resume` | Summarize project context | `/resume NOVA AI Office` |
| `/progress` | Show task completion | `/progress NOVA AI Office` |
| `/continue` | Show actionable recent context | `/continue NOVA AI Office` |

Existing Sprint 1 commands remain available: `/start`, `/help`, and `/status`.

Task statuses are `todo`, `doing`, `done`, and `cancelled`. Use a numeric task ID when duplicate task titles exist; `/tasks` displays each ID. A completed timestamp is set in UTC when a task becomes `done` and cleared when it is reopened.

`/session` accepts a project and summary, with optional completed items and next action. `/sessions` defaults to five entries and accepts a limit from 1 to 10. `/continue` selects its recommended next action in this order: the latest session action, a priority-ranked `doing` task, a priority-ranked `todo` task, then a no-pending-action message.

## Backup and reset

Back up the SQLite file only while NOVA is stopped, or use SQLite's backup mechanism. Do not copy a live database file while the bot is writing to it. To reset local memory, stop NOVA and delete the configured database file plus any adjacent `-wal` or `-shm` files. This is irreversible unless a backup exists.

## Security limitations

Workspace Memory is local SQLite storage, not encrypted-at-rest application storage. Do not enter bot tokens, passwords, API keys, environment values, or other secrets into notes, decisions, task descriptions, or sessions. The database is excluded from Git and is not synchronized to Google Drive or any cloud service in Sprint 2.

## Documentation

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Security](docs/SECURITY.md)
- [Sprint 1.1](docs/SPRINT_01.md)
- [Agent Registry](docs/AGENT_REGISTRY.md)

## Scope boundary

Sprint 2 preserves Sprint 1's Telegram authorization and commands while adding local SQLite Workspace Memory. Google Drive, Gmail, Calendar, AI APIs, vector databases, and cloud databases are not implemented.

## License

License terms are intentionally undecided pending project governance.
