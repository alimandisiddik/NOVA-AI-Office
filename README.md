# NOVA AI Office

NOVA AI Office is the project foundation for an executive multi-agent AI office: a governed workspace where specialized agents, services, workflows, prompts, and institutional knowledge can be introduced deliberately over time.

## Sprint 1.1 status

This repository contains the project foundation plus a local-only Telegram bot for the configured owner. It intentionally includes no cloud services, no AI provider SDKs, no credentials, and no business automation.

## Repository layout

```text
agents/       Agent specifications and future implementations
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
4. Install dependencies only when a future sprint explicitly adds them.

No dependencies are required in Sprint 1.1.

## Documentation

- [Vision](docs/VISION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [Roadmap](docs/ROADMAP.md)
- [Security](docs/SECURITY.md)
- [Sprint 1.1](docs/SPRINT_01.md)
- [Agent Registry](docs/AGENT_REGISTRY.md)

## Scope boundary

Sprint 1 includes only local Telegram polling for one configured user. Google Drive, Gmail, Calendar, AI APIs, and cloud services are not implemented.

## License

License terms are intentionally undecided pending project governance.
