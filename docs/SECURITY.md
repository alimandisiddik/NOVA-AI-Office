# Security

## Security posture

NOVA AI Office is designed around least privilege, explicit human approval, secure configuration, and auditable operations. Sprint 1 uses one Telegram bot token from an uncommitted local `.env` file and does not connect to cloud productivity services or AI providers.

## Baseline controls

1. **Secret hygiene** — use `.env` only for local configuration; it is excluded from version control. Commit `.env.example` with placeholders only.
2. **Least privilege** — future integrations must request only the minimum scopes required for their approved capability.
3. **Approval gates** — workflows that affect external systems, data, or people require explicit policy-defined approval.
4. **Auditability** — future workflows must record actor, action, source context, approval state, and outcome.
5. **Data minimization** — collect and retain only information necessary for a stated operational purpose.
6. **Separation of concerns** — agents, services, prompts, and integrations must not share credentials or bypass authorization boundaries.

## Future security requirements

- Threat-model every external integration before implementation.
- Store production secrets in an approved secret manager.
- Define data classification, retention, deletion, and incident-response policies.
- Add dependency scanning, secret scanning, and automated security checks to continuous integration.
- Review prompt injection and untrusted-content handling before enabling knowledge retrieval or agentic tool use.

## Reporting

Report suspected secrets exposure or security defects through the project’s designated private security channel once governance is established. Do not include secrets in issues, logs, commits, or test fixtures.
