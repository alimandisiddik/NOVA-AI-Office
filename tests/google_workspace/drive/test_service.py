"""Tests for Secure Drive Read Service."""

import io
import os
import json
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.google_workspace.drive.models import AllowlistedFolder, DriveFileMetadata
from app.google_workspace.drive.service import (
    DriveReadService,
    DriveReadError,
    SecurityError,
    ConfigError,
)

class TestDriveReadService(unittest.TestCase):
    def setUp(self):
        self.factory = MagicMock()
        self.mock_service = MagicMock()
        self.factory.get_service.return_value = self.mock_service
        self.allowed_folders = [
            AllowlistedFolder(folder_id="folder123", alias="allowed_1"),
            AllowlistedFolder(folder_id="folder456", alias="allowed_2"),
        ]
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir).resolve()
        self.svc = DriveReadService(self.factory, self.allowed_folders, self.workspace_dir)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_config_error_on_relative_path(self):
        with self.assertRaises(ConfigError):
            DriveReadService(self.factory, self.allowed_folders, Path("relative/path"))

    def test_get_metadata_success(self):
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "test.txt",
            "mimeType": "text/plain",
            "size": "100",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["folder123"],
        }

        meta = self.svc.get_metadata("file1")
        self.assertEqual(meta.file_id, "file1")
        self.assertEqual(meta.name, "test.txt")
        self.assertEqual(meta.size_bytes, 100)

    def test_get_metadata_not_allowed(self):
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "test.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["unallowed"],
        }

        with self.assertRaisesRegex(SecurityError, "not belong to an allowed folder"):
            self.svc.get_metadata("file1")

    def test_search_files(self):
        self.mock_service.files().list().execute.return_value = {
            "files": [
                {
                    "id": "file1",
                    "name": "test.txt",
                    "mimeType": "text/plain",
                    "modifiedTime": "2023-10-01T12:00:00Z",
                    "parents": ["folder123"],
                }
            ]
        }

        page = self.svc.search_files(folder_id="folder123", name_query="test")
        self.assertEqual(len(page.results), 1)
        self.assertEqual(page.results[0].metadata.file_id, "file1")

    def test_search_files_unallowed_folder(self):
        with self.assertRaises(SecurityError):
            self.svc.search_files(folder_id="unallowed")

    def test_identify_latest_version(self):
        def get_mock(*args, **kwargs):
            fid = kwargs.get("fileId")
            res_mock = MagicMock()
            if fid == "f1":
                res_mock.execute.return_value = {"id": "f1", "name": "f1", "mimeType": "text/plain", "modifiedTime": "2023-10-01T10:00:00Z", "parents": ["folder123"]}
            elif fid == "f2":
                res_mock.execute.return_value = {"id": "f2", "name": "f2", "mimeType": "text/plain", "modifiedTime": "2023-10-01T12:00:00Z", "parents": ["folder123"]}
            else:
                res_mock.execute.side_effect = Exception("not found")
            return res_mock

        self.mock_service.files().get.side_effect = get_mock

        latest = self.svc.identify_latest_version(["f1", "f2"])
        self.assertEqual(latest.file_id, "f2")

    def test_download_working_copy(self):
        self.mock_service.files().get_media.return_value = "fake_request"
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "test.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["folder123"],
        }

        # Inject MediaIoBaseDownload directly at the googleapiclient boundary
        import sys
        mock_module = MagicMock()
        class FakeDownloader:
            def __init__(self, fd, req):
                self.fd = fd
                self.req = req
                self.done = False
            def next_chunk(self):
                if not self.done:
                    self.fd.write(b"hello")
                    self.done = True
                    return None, True
                return None, True

        mock_module.MediaIoBaseDownload = FakeDownloader
        sys.modules['googleapiclient.http'] = mock_module

        try:
            res = self.svc.download_working_copy("file1")
            self.assertEqual(res.bytes_written, 5)
            self.assertTrue(res.local_path.endswith(".txt"))
            self.assertIn("test", res.local_path)
            # Verify SHA-256
            import hashlib
            self.assertEqual(res.sha256_checksum, hashlib.sha256(b"hello").hexdigest())
            self.assertTrue(os.path.exists(res.local_path))
        finally:
            sys.modules.pop('googleapiclient.http', None)

    def test_export_working_copy(self):
        self.mock_service.files().export_media.return_value = "fake_request"
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "doc",
            "mimeType": "application/vnd.google-apps.document",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["folder123"],
        }

        import sys
        mock_module = MagicMock()
        class FakeDownloader:
            def __init__(self, fd, req):
                self.fd = fd
                self.req = req
                self.done = False
            def next_chunk(self):
                if not self.done:
                    self.fd.write(b"%PDF")
                    self.done = True
                    return None, True
                return None, True

        mock_module.MediaIoBaseDownload = FakeDownloader
        sys.modules['googleapiclient.http'] = mock_module

        try:
            res = self.svc.export_working_copy("file1")
            self.assertEqual(res.bytes_written, 4)
            self.assertTrue(res.local_path.endswith(".pdf"))
            import hashlib
            self.assertEqual(res.sha256_checksum, hashlib.sha256(b"%PDF").hexdigest())
            self.assertTrue(os.path.exists(res.local_path))
        finally:
            sys.modules.pop('googleapiclient.http', None)

    def test_download_unsupported_mime(self):
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "exec.exe",
            "mimeType": "application/x-msdownload",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["folder123"],
        }

        with self.assertRaisesRegex(SecurityError, "not approved"):
            self.svc.download_working_copy("file1")

if __name__ == "__main__":
    unittest.main()
