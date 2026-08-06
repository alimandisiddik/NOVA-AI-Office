# Google Drive Read-Only Integration

The Google Drive service in NOVA operates under a strictly non-mutating framework.

## Core Properties
1. **Never writes**: The service cannot upload, update, edit, move, share, or trash any items.
2. **Scoping**: Built on top of the `.readonly` scope.
3. **Allowlist Mechanism**: Access is strictly limited to an approved set of pre-configured Google Drive folder IDs.

## Service Interface
- `search_files`: Filters items bounded strictly by an internal allowlisted folder and enforces multi-page deduplication, recursion mitigation, and exact `limit` boundaries correctly tracking unique files per global limit.
- `identify_latest_version`: Breaks timestamp ties deterministically while rejecting pointer items like shortcuts for reliable mapping.
- `export_working_copy` & `download_working_copy`: Streams documents safely locally verifying file byte size constraints block-by-block dynamically, producing a `WorkingCopyResult` with a validated SHA-256 hash.

## Query Construction & Pagination
Raw caller filters are escaped sequentially for backslashes and single quotes. Control characters are immediately rejected. The internal service strictly maintains `'{folder_id}' in parents` guaranteeing API isolation. Bounded pagination ensures seen-token set validation blocking loop recursion.

## MIME / Export Policy
Downloads restrict exactly against a whitelist (PDF, DOCX, XLSX, PPTX, text). G-Suite apps dynamically transform strictly against `ExportRule` boundaries (`.pdf`, `.xlsx`). Binaries map extensions exactly to the target rules blocking original untrusted names (e.g., `report.pdf.exe` correctly limits into `report_pdf_exe.pdf`).

## Streaming & Atomic Persistence
The `MediaIoBaseDownload` streams directly to disk utilizing OS-level file creation constraints (`os.O_EXCL`) avoiding over-writes. `os.fsync` operates securely against the temp chunk. On success, `os.replace` commits atomically against the original root boundaries. On dynamic byte size boundary failure, temporary partial files are wiped completely via fallback blocks.

## Symlink and Path Protection
The target root `workspace_dir` maps explicitly via `.resolve()` blocking internal directory manipulations natively over macOS boundaries (/tmp or /var limits natively execute). The destination is strictly checked for symlink trickery before streaming, and revalidated dynamically right before `os.replace` runs.

## Audit Privacy
No unhashed IDs, paths, tokens, file structures, or Google credential paths are placed into logging sinks. `id_hash` uses SHA-256. Categories structure error classification (e.g., `success`, `denied_too_large`).

## Exception Taxonomy
Broad provider/network boundaries are safely mapped into `DriveProviderError` retaining `retryable` status codes (Rate Limit, Quota, Timeout) isolating network bounds. Native Python Programming bounds (`NameError`, `AttributeError`) are strictly preserved executing un-altered over the execution bounds allowing diagnostic isolation natively.

## Testing Integration
Testing boundaries execute strictly via Native execution (without internal method stubs) mapping exactly over `tests/google_workspace/drive/`.

## Operational Limitations
- Does not expose arbitrary drive traversal (`limit` parameter binds globally).
- Checksums calculate via local streaming state; Google's API metadata hash is unused.
