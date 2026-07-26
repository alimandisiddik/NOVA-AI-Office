# Architecture

## Foundation approach

Sprint 1.1 establishes repository-level boundaries before implementation begins. The layout separates agent definitions, business services, orchestration workflows, prompts, reusable templates, knowledge assets, tests, and maintenance scripts.

```text
Inputs and future integrations
            |
            v
        workflows/  <---->  agents/
            |                  |
            v                  v
        services/  <---->  prompts/
            |
            v
 knowledge/ and templates/
```

## Boundary responsibilities

| Area | Responsibility |
| --- | --- |
| `agents/` | Agent roles, capabilities, constraints, and future implementations. |
| `services/` | Reusable application services and domain logic. |
| `workflows/` | Orchestration definitions, approval points, and task sequencing. |
| `prompts/` | Versioned prompt instructions separated from application code. |
| `templates/` | Reusable structured output and workflow templates. |
| `knowledge/` | Governed, source-aware knowledge assets and indexes. |
| `tests/` | Unit, integration, and workflow validation as functionality is added. |
| `scripts/` | Developer and operational utilities that do not belong in runtime services. |

## Architectural constraints

- External integrations remain isolated behind future service adapters.
- Credentials belong in environment configuration or an approved secrets manager, never in source control.
- Agents must not directly bypass workflow approval or audit boundaries.
- Prompt assets must be versioned and tested alongside the workflows that use them.
- Any persistence layer introduced later must define data ownership, retention, and access controls.

## Current implementation status

Sprint 1 includes a minimal local Telegram polling module for one configured user. There are no cloud-service adapters, API clients for Google or AI providers, databases, queues, or AI model calls.
