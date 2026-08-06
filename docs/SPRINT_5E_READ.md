# Sprint 5E — Google Drive Read-Only

## Objective
Provide a secure, strictly read-only Google Drive service that returns structured file results and safe local working copies to the Executive Control Tower.

## Scope
- Drive read service with strict allowlists (`search_files`, `list_recent_files`, `get_metadata`, `identify_latest_version`, `download_working_copy`, `export_working_copy`).
- Secure DTO wrappers enforcing schema (e.g., `DriveFileMetadata`, `WorkingCopyResult` with SHA-256 checksum).
- Bounded file size streaming using Native OS `O_CREAT | O_EXCL` flags, ensuring no in-memory buffers bypass limits.
- Symlink macOS-compatible mitigation establishing a trusted root `workspace_dir.resolve()` mapping.
- Unique-result pagination loop detection, duplicate deduplication across multi-page boundaries.
- Strict hashing of file IDs for logging; no clear text paths or IDs left in logs.
- Typed exception mapping isolating Python programming defects from Google provider bounds.

## Out of scope
- Drive write, permission updates, share modifications.
- Calendar or external app integrations not pertaining strictly to the `.readonly` Google Drive specification.
- Task execution wrappers or agent hooks.
- DOCX mutating.

## Deliverables
- Implementation of `DriveReadService` within `app/google_workspace/drive/service.py` integrating the `googleapiclient.http.MediaIoBaseDownload` securely.
- Extensive test coverage `tests/google_workspace/drive/` strictly utilizing end-to-end `MediaIoBaseDownload` injection directly over pytest without mocking internal structural methods.
- Modification of `factory.py` exposing the "drive" API securely.

## Security Constraints
- All downloads are streamed direct to disk via atomic `f.flush()` and `os.fsync()` ensuring safe limits prior to `os.replace`.
- Boolean configurations natively excluded rejecting `True`/`False` bounds.
- Filenames exclusively originate via secure mapping against the internal MIME database blocking `report.pdf.exe` obfuscation limits.

## Acceptance Criteria
- Drive Read service handles all paths locally via read-only constraints.
- Tests enforce deterministic behaviour under all error structures (OOM avoidance, path traversal avoidance, infinite loops).
- Real missing `MediaIoBaseDownload` dependency paths crash explicitly with `ConfigError`.

## Recommendation for Claude Review
Claude, when reviewing this module, ensure special attention is given to:
1. `_stream_download_safe` function ensuring atomic POSIX flag behaviour, `os.fsync` boundary tracking and deterministic hashing limits without buffering bypasses.
2. The regex escaping used in `search_files` (`name contains` queries) within `drive/service.py` ensuring double escaping properly mitigates Drive string injection techniques.
