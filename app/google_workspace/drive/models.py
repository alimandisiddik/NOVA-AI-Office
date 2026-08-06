"""Data Transfer Objects for the secure Google Drive read-only service."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AllowlistedFolder:
    """Represents an approved, pre-configured folder for read-only access."""
    folder_id: str
    alias: str


@dataclass(frozen=True)
class ExportRule:
    """Defines how a specific Google Workspace MIME type should be exported safely."""
    source_mime: str
    target_mime: str
    target_extension: str


@dataclass(frozen=True)
class DriveFileMetadata:
    """Safe, strictly typed representation of Google Drive file metadata."""
    file_id: str
    name: str
    mime_type: str
    size_bytes: int | None
    modified_time: datetime
    parents: tuple[str, ...] = field(default_factory=tuple)
    md5_checksum: str | None = None
    version: str | None = None


@dataclass(frozen=True)
class DriveSearchResult:
    """A single item matched in a search."""
    metadata: DriveFileMetadata
    # Safe subset of fields provided to calling service, avoiding raw provider data


@dataclass(frozen=True)
class DrivePage:
    """Paginated collection of safe drive search results."""
    results: tuple[DriveSearchResult, ...]
    next_page_token: str | None


@dataclass(frozen=True)
class WorkingCopyResult:
    """Safe return value containing the path to a downloaded/exported read-only file."""
    local_path: str
    metadata: DriveFileMetadata
    bytes_written: int
    sha256_checksum: str



class DriveReadError(Exception):
    """Base error for Drive read operations, sanitizing underlying provider exceptions."""

class ConfigError(DriveReadError):
    """Invalid configuration for the Drive service."""

class SecurityError(DriveReadError):
    """Attempted unauthorized or out-of-bounds access."""

class SizeLimitError(SecurityError):
    """File size exceeds configured limits."""

class PathSecurityError(SecurityError):
    """Path traversal or symlink violation detected."""

class UnsafeMimeError(SecurityError):
    """File type not approved for operation."""

class DriveProviderError(DriveReadError):
    """Sanitized Google API provider error."""
    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.retryable = retryable
