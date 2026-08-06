"""Secure Drive read-only implementation."""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from app.google_workspace.auth import AuthError, GoogleDependencyError
from app.google_workspace.factory import GoogleClientFactory
from app.google_workspace.drive.models import (
    ConfigError,
    SecurityError,
    SizeLimitError,
    PathSecurityError,
    DriveProviderError,
    DriveReadError,
    AllowlistedFolder,
    DriveFileMetadata,
    DrivePage,
    DriveSearchResult,
    ExportRule,
    WorkingCopyResult,
)


logger = logging.getLogger(__name__)





# Approved MIME types mapping to safe export formats
APPROVED_EXPORTS: dict[str, ExportRule] = {
    "application/vnd.google-apps.document": ExportRule(
        source_mime="application/vnd.google-apps.document",
        target_mime="application/pdf",
        target_extension=".pdf",
    ),
    "application/vnd.google-apps.spreadsheet": ExportRule(
        source_mime="application/vnd.google-apps.spreadsheet",
        target_mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        target_extension=".xlsx",
    ),
    "application/vnd.google-apps.presentation": ExportRule(
        source_mime="application/vnd.google-apps.presentation",
        target_mime="application/pdf",
        target_extension=".pdf",
    ),
}

APPROVED_DOWNLOAD_MIMES = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "text/plain",
}


def _sanitize_filename_stem(name: str) -> str:
    """Make filename stem safe for local storage, ignoring original extensions."""
    import re
    # Remove path separators, null bytes, control characters, and reserved names
    name = re.sub(r'[\x00-\x1F/\\:<>|"\?\*]', '_', name)

    # Remove dot path structures or previous extensions by taking the stem manually
    name = name.replace('.', '_')

    # Windows reserved names
    upper_name = name.upper()
    reserved = {"CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if upper_name in reserved:
        name = f"_{name}_"

    if not name:
        name = "unnamed_file"

    return name[:200]


class DriveReadService:
    """Secure, read-only Drive service."""

    def __init__(
        self,
        factory: GoogleClientFactory,
        allowed_folders: list[AllowlistedFolder],
        workspace_dir: Path,
        max_file_size_bytes: int = 50 * 1024 * 1024, # 50MB
        max_results_limit: int = 100,
    ):
        if type(max_file_size_bytes) is not int or max_file_size_bytes <= 0 or max_file_size_bytes > 5 * 1024 * 1024 * 1024:
            raise ConfigError("max_file_size_bytes must be a strictly positive integer within bounds (5GB max)")
        if type(max_results_limit) is not int or max_results_limit <= 0 or max_results_limit > 1000:
            raise ConfigError("max_results_limit must be a strictly positive integer within bounds (1000 max)")

        if not allowed_folders:
            raise ConfigError("allowed_folders cannot be empty")

        self._allowed_folders = {}
        aliases = set()
        for f in allowed_folders:
            if not f.folder_id or not f.alias:
                 raise ConfigError("Folder ID and alias must be non-empty strings")
            if f.alias in aliases:
                 raise ConfigError(f"Duplicate alias detected: {f.alias}")
            aliases.add(f.alias)
            self._allowed_folders[f.folder_id] = f

        self._factory = factory

        # Verify the workspace config isn't a symlink leaf directly
        if workspace_dir.is_symlink():
            raise ConfigError("Workspace directory cannot be a symlink")

        # Instead of walking up to / (which blocks /var or /tmp on mac), we establish a trusted root.
        self._workspace_dir = workspace_dir.resolve()
        self._max_file_size_bytes = max_file_size_bytes
        self._max_results_limit = max_results_limit

        if not self._workspace_dir.is_absolute():
            raise ConfigError("Workspace directory must be an absolute path")

        self._workspace_dir.mkdir(parents=True, exist_ok=True)
        if not workspace_dir.is_absolute():
            raise ConfigError("Workspace directory must be an absolute path")
        if not os.access(self._workspace_dir, os.W_OK | os.X_OK):
            raise ConfigError("Workspace directory is not writable")

    def _get_service(self) -> Any:
        """Get the authenticated Drive API client."""
        try:
            return self._factory.get_service("drive", "v3")
        except AuthError as e:
            raise DriveReadError("Authentication error connecting to Drive") from e
        except GoogleDependencyError as e:
            raise DriveReadError("Google API client not available") from e
        except Exception as e:
            raise DriveReadError("Unexpected error initializing Drive client") from e

    def _audit(self, operation: str, file_id: str | None, mime_type: str | None, status: str, extra: dict[str, Any] | None = None) -> None:
        """Internal audit logging."""
        id_hash = hashlib.sha256(file_id.encode('utf-8')).hexdigest()[:16] if file_id else None
        details = extra or {}
        logger.info(
            "DriveReadAudit | op=%s status=%s id_hash=%s mime=%s details=%s",
            operation, status, id_hash, mime_type, details
        )

    def _safe_metadata(self, api_file: dict[str, Any]) -> DriveFileMetadata:
        try:
            size = int(api_file["size"]) if "size" in api_file else None
            # e.g., '2023-01-01T12:00:00.000Z'
            mod_time = datetime.fromisoformat(api_file["modifiedTime"].replace("Z", "+00:00"))
            return DriveFileMetadata(
                file_id=api_file["id"],
                name=api_file["name"],
                mime_type=api_file["mimeType"],
                size_bytes=size,
                modified_time=mod_time,
                parents=tuple(api_file.get("parents", [])),
                md5_checksum=api_file.get("md5Checksum"),
                version=api_file.get("version"),
            )
        except (KeyError, ValueError) as e:
            raise DriveReadError("Malformed metadata received from Google Drive") from e

    def _execute_api_call(self, call_func: Callable[[], Any], error_msg: str) -> Any:
        try:
            return call_func()
        except DriveReadError:
            raise
        except Exception as e:
            err_name = type(e).__name__

            # If it's a known python programming defect, don't map to DriveProviderError!
            if isinstance(e, (NameError, AttributeError, TypeError, ValueError, AssertionError, KeyError)):
                raise  # Let programming defects bubble up normally

            # If it's from googleapiclient (usually HttpError)
            if err_name == "HttpError":
                self._audit("api_call", None, None, "provider_failure", {"error_type": err_name})
                retryable = False
                err_str = str(e).lower()

                status_code = getattr(e, 'status_code', None)
                if status_code in (429, 500, 502, 503, 504) or "timeout" in err_str or "rate limit" in err_str:
                    retryable = True

                # We specifically don't leak `str(e)` to avoid exposing internal paths/tokens.
                raise DriveProviderError(error_msg, retryable=retryable) from None

            # Map other generic network/dependency issues safely
            self._audit("api_call", None, None, "network_failure", {"error_type": err_name})
            retryable = "timeout" in str(e).lower() or "connection" in str(e).lower()
            raise DriveProviderError(error_msg, retryable=retryable) from None

    def list_allowed_folders(self) -> tuple[AllowlistedFolder, ...]:
        return tuple(self._allowed_folders.values())

    def get_metadata(self, file_id: str) -> DriveFileMetadata:
        """Retrieve safe metadata for a specific file, ensuring it belongs to an allowed folder."""
        service = self._get_service()
        fields = "id, name, mimeType, size, modifiedTime, parents, md5Checksum, version"

        def _get():
            return service.files().get(
                fileId=file_id,
                fields=fields,
                supportsAllDrives=True
            ).execute()

        raw_file = self._execute_api_call(_get, "Failed to retrieve file metadata")
        meta = self._safe_metadata(raw_file)

        # Enforce folder allowlist
        if not meta.parents:
            self._audit("get_metadata", file_id, meta.mime_type, "denied_no_parent")
            raise SecurityError("File does not belong to any allowed folder")

        if not any(p in self._allowed_folders for p in meta.parents):
            self._audit("get_metadata", file_id, meta.mime_type, "denied_unallowed_parent")
            raise SecurityError("File does not belong to an allowed folder")

        self._audit("get_metadata", file_id, meta.mime_type, "success", {"size": meta.size_bytes})
        return meta

    def search_files(
        self,
        folder_id: str,
        name_query: str | None = None,
        mime_type: str | None = None,
        page_token: str | None = None,
        limit: int = 50,
    ) -> DrivePage:
        """Search files within a strictly specified allowlisted folder."""
        if folder_id not in self._allowed_folders:
            self._audit("search_files", None, None, "denied_unallowed_folder")
            raise SecurityError("Folder ID is not allowlisted")

        limit = min(limit, self._max_results_limit)

        # Build safe query string
        # Prevent user from escaping the query and accessing other folders
        query_parts = [f"'{folder_id}' in parents"]
        if name_query:
            # Escape backslashes first, then quotes
            safe_name = name_query.replace('\\', '\\\\').replace("'", "\\'")
            # Reject control characters
            if re.search(r'[\x00-\x1F]', safe_name):
                 raise SecurityError("Control characters not allowed in name query")
            query_parts.append(f"name contains '{safe_name}'")
        if mime_type:
            if mime_type not in APPROVED_DOWNLOAD_MIMES and mime_type not in APPROVED_EXPORTS:
                 raise SecurityError("MIME type not allowed in query")
            safe_mime = mime_type.replace('\\', '\\\\').replace("'", "\\'")
            query_parts.append(f"mimeType = '{safe_mime}'")

        # always exclude trashed
        query_parts.append("trashed = false")

        query_str = " and ".join(query_parts)

        service = self._get_service()
        fields = "nextPageToken, files(id, name, mimeType, size, modifiedTime, parents, md5Checksum, version)"

        seen_tokens = set()
        current_token = page_token

        all_results = []

        def _search_page():
            return service.files().list(
                q=query_str,
                spaces="drive",
                fields=fields,
                pageToken=current_token,
                pageSize=limit, # Drive API page size
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
                orderBy="modifiedTime desc"
            ).execute()

        deduped = {}
        while len(deduped) < limit:
            response = self._execute_api_call(_search_page, "Search operation failed")

            for f in response.get("files", []):
                try:
                    res = DriveSearchResult(self._safe_metadata(f))
                    if res.metadata.file_id not in deduped:
                        deduped[res.metadata.file_id] = res
                        if len(deduped) >= limit:
                            break
                except DriveReadError:
                    pass # Skip malformed files securely

            next_token = response.get("nextPageToken")
            if not next_token:
                current_token = None
                break

            if next_token in seen_tokens:
                raise DriveReadError("Pagination loop detected from provider")

            seen_tokens.add(next_token)
            current_token = next_token

        final_results = list(deduped.values())
        # Re-sort to maintain modifiedTime desc after deduplication
        final_results.sort(key=lambda x: x.metadata.modified_time, reverse=True)

        self._audit("search_files", None, None, "success", {"count": len(final_results)})
        return DrivePage(
            results=tuple(final_results[:limit]),
            next_page_token=current_token
        )

    def list_recent_files(self, folder_id: str, limit: int = 10) -> DrivePage:
        """List recently modified files in an allowlisted folder."""
        return self.search_files(folder_id=folder_id, limit=limit)

    def identify_latest_version(self, file_ids: list[str]) -> DriveFileMetadata:
        """Given multiple IDs, return the one with the latest modified time."""
        if not file_ids:
            raise DriveReadError("No file IDs provided")

        metas = []
        for fid in file_ids:
            try:
                meta = self.get_metadata(fid)
                if meta.mime_type == "application/vnd.google-apps.shortcut":
                    raise SecurityError("Shortcuts are not supported directly in latest version check")
                metas.append(meta)
            except Exception as e:
                pass

        if not metas:
            raise DriveReadError("No allowed or valid files found among IDs")

        # Tie breaker: modified time first, then ID
        return max(metas, key=lambda m: (m.modified_time, m.file_id))

    def _stream_download_safe(self, request, stem_name: str, extension: str) -> WorkingCopyResult:
        from googleapiclient.http import MediaIoBaseDownload

        safe_stem = _sanitize_filename_stem(stem_name)
        import uuid
        unique_name = f"{uuid.uuid4().hex[:8]}_{safe_stem}{extension}"

        # Verify symlink protection on destination directory without dereferencing early
        def _check_no_symlinks(path: Path) -> None:
            current = path
            while current != current.parent:
                if current.exists() and current.is_symlink():
                    raise PathSecurityError(f"Symlink detected in path component: {current}")
                current = current.parent

        _check_no_symlinks(self._workspace_dir)

        dest_dir = self._workspace_dir.resolve()
        local_path = (dest_dir / unique_name).resolve()

        if not str(local_path).startswith(str(dest_dir)):
            raise PathSecurityError("Path traversal detected")

        if local_path.exists() and local_path.is_symlink():
            raise PathSecurityError("Destination path cannot be a symlink")

        temp_path = local_path.with_suffix('.tmp')

        written = 0
        sha256_hash = hashlib.sha256()

        try:
            # Atomic permissions: owner read/write only

            fd = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, 'wb') as f:
                    from googleapiclient.http import MediaIoBaseDownload
                    downloader = MediaIoBaseDownload(f, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()

                        f.flush()
                        current_size = os.fstat(fd).st_size
                        if current_size > self._max_file_size_bytes:
                            raise SizeLimitError(f"File size exceeded maximum {self._max_file_size_bytes}")

                    f.flush()
                    os.fsync(fd)
            except Exception:
                raise

            with open(temp_path, 'rb') as f:
                while chunk := f.read(8192):
                    sha256_hash.update(chunk)

            written = temp_path.stat().st_size
            if written > self._max_file_size_bytes:
                raise SizeLimitError(f"File size exceeded maximum {self._max_file_size_bytes}")

            if local_path.exists() and local_path.is_symlink():
                 raise PathSecurityError("Destination path became a symlink")

            os.replace(temp_path, local_path)

            # fsync parent directory on POSIX
            if os.name == 'posix':
                try:
                    dir_fd = os.open(dest_dir, os.O_RDONLY)
                    os.fsync(dir_fd)
                    os.close(dir_fd)
                except OSError:
                    pass


            return WorkingCopyResult(
                local_path=str(local_path),
                metadata=None, # Will be set by caller
                bytes_written=written,
                sha256_checksum=sha256_hash.hexdigest()
            )
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise

    def export_working_copy(self, file_id: str) -> WorkingCopyResult:
        """Export a Google Workspace document to a safe static format."""
        meta = self.get_metadata(file_id)

        if meta.mime_type not in APPROVED_EXPORTS:
            self._audit("export", file_id, meta.mime_type, "denied_unsupported_type")
            raise SecurityError("File type not approved for export")

        rule = APPROVED_EXPORTS[meta.mime_type]
        service = self._get_service()

        def _export():
            try:
                from googleapiclient.http import MediaIoBaseDownload
            except ImportError as error:
                raise ConfigError("Google API client not available") from error

            request = service.files().export_media(fileId=file_id, mimeType=rule.target_mime)
            return self._stream_download_safe(request, meta.name, rule.target_extension)

        res = self._execute_api_call(_export, "Export operation failed")

        # update metadata
        res = WorkingCopyResult(
            local_path=res.local_path,
            metadata=meta,
            bytes_written=res.bytes_written,
            sha256_checksum=res.sha256_checksum
        )

        self._audit("export", file_id, meta.mime_type, "success", {"size": res.bytes_written})
        return res

    def download_working_copy(self, file_id: str) -> WorkingCopyResult:
        """Download a safe binary file (e.g. PDF, Office format)."""
        meta = self.get_metadata(file_id)

        if meta.mime_type not in APPROVED_DOWNLOAD_MIMES:
            self._audit("download", file_id, meta.mime_type, "denied_unsupported_type")
            raise SecurityError("File type not approved for direct download")

        if meta.size_bytes and meta.size_bytes > self._max_file_size_bytes:
             self._audit("download", file_id, meta.mime_type, "denied_too_large")
             raise SecurityError("File size exceeds limit before download")

        service = self._get_service()

        def _download():
            try:
                from googleapiclient.http import MediaIoBaseDownload
            except ImportError as error:
                raise ConfigError("Google API client not available") from error

            ext_map = {
                "application/pdf": ".pdf",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
                "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
                "text/plain": ".txt"
            }
            target_extension = ext_map[meta.mime_type]

            request = service.files().get_media(fileId=file_id)
            return self._stream_download_safe(request, meta.name, target_extension)

        res = self._execute_api_call(_download, "Download operation failed")

        res = WorkingCopyResult(
            local_path=res.local_path,
            metadata=meta,
            bytes_written=res.bytes_written,
            sha256_checksum=res.sha256_checksum
        )

        self._audit("download", file_id, meta.mime_type, "success", {"size": res.bytes_written})
        return res
