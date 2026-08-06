# Sprint 5C — Google Workspace Foundation

## Objective
Establish the secure Google OAuth desktop flow foundation, a least-privilege scope registry, and secure credential and token lifecycle management, handling refresh-token behavior, reconnects, revokes, and audit metadata without storing sensitive token contents.

## Required outcomes
- Google OAuth desktop flow foundation;
- least-privilege scope registry;
- secure credential and token lifecycle;
- refresh-token handling;
- reconnect and revoke operations;
- Google client factory;
- safe connection status;
- audit metadata without token contents;
- configuration validation;
- backward compatibility.

## Out of scope
- Calendar business operations;
- Drive search, download, upload, or file mutation;
- Telegram commands unless explicitly required by the approved specification;
- dissertation features.

## Security constraints
Never commit:
- client secrets;
- access tokens;
- refresh tokens;
- credential JSON;
- local token databases.

Use mocked Google/OAuth clients in tests. No real Google network calls.

## OAuth callback and port policy

The desktop callback listener binds only to `localhost`. `GOOGLE_OAUTH_PORT=0`
means the operating system selects an ephemeral local port. A configured port
must be an integer in the inclusive range `1..65535` and is forwarded unchanged
to the OAuth desktop flow. OAuth flow failures are represented only by stable
failure categories; raw provider responses, authorization codes, exception
text, token material, and credential paths are not stored in audit metadata.

## Credential safety

The configured client-secrets JSON is the source of the expected OAuth client
identity: `installed.client_id`. Credentials loaded from storage, returned by a
new desktop flow, and refreshed on demand must match that identity and have the
exact canonical requested scope set—partial, missing, unknown, or excess scopes
fail closed. `local_disconnect()` deletes only local cached credentials; it does
not claim remote Google token revocation. Refresh is on-demand only; no
background token refresh is implemented.

## Token-file policy

Token files are JSON-only, use `0700` parent directories and `0600` files, and
are written with a restrictive temporary file, `fsync`, and `os.replace`.
Symlinked target paths and direct parent directories are rejected and checked
again immediately before replacement. As with portable filesystem APIs, a
single-user local-desktop TOCTOU window remains if another process changes the
path between the final check and replacement; NOVA mitigates this with
owner-only directories and restrictive permissions.
