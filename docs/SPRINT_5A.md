# Sprint 5A — Always-On Runtime

## Objective
Provide an always-on runtime for NOVA AI Office using macOS `launchd`, ensuring the bot runs automatically without requiring an active Terminal window, restarts on failure, and manages logs safely.

## Specifications and Outcomes
- **macOS launchd service**: Configuration (`.plist`) tailored for the local environment.
- **Automatic startup**: Service config uses `RunAtLoad` for immediate start.
- **Restart after unexpected failure**: Service config uses `KeepAlive` based on successful exit status.
- **Graceful stop**: Handled by python-telegram-bot's default `SIGTERM` handling, triggered by `launchctl stop`.
- **Single-instance protection**: A file lock (`data/nova.lock`) explicitly prevents duplicate runs started via `python -m app.run_singleton` (the launchd-managed entry point). See Accepted Limitations.
- **Health check & service status**: A bash wrapper (`scripts/service.sh`) provides `health`, `status`, `start`, `stop`, `install`, and `uninstall` commands.
- **Bounded logging**: Handled natively in Python via `RotatingFileHandler` ensuring `nova.log` does not grow unbounded.
- **Secrets safety**: Continues to omit sensitive payloads from operational logs.
- **Headless mode**: No open Terminal required.
- **Preserved capabilities**: No changes to existing Telegram or provider logic.

## Accepted Limitations
1. The `fcntl` single-instance lock is acquired only in `app/run_singleton.py`. Running `python -m app.main` directly bypasses the lock entirely and can start a second, unlocked instance. This is an accepted risk: the launchd service and the documented manual-start path (`python -m app.run_singleton`) are the only supported ways to run the bot outside of tests.
2. `scripts/service.sh health` confirms a live PID is registered with launchd; there is no application-level heartbeat. A service stuck or crash-looping between two health checks can report healthy at the moment of inspection. A true heartbeat is deferred to a future sprint.

## Exclusions
- Telegram agent operations
- Google integration
- Dissertation features
- Git automation
