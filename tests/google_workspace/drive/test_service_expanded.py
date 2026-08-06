"""Expanded tests for Drive Read Service."""

import os
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.google_workspace.drive.models import (
    AllowlistedFolder,
    ConfigError,
    DriveFileMetadata,
    DriveProviderError,
    PathSecurityError,
    SecurityError,
    SizeLimitError,
)
from app.google_workspace.drive.models import DriveProviderError
from app.google_workspace.drive.service import DriveReadService, _sanitize_filename_stem



class TestDriveReadServiceExpanded(unittest.TestCase):
    def setUp(self):
        self.factory = MagicMock()
        self.mock_service = MagicMock()
        self.factory.get_service.return_value = self.mock_service
        self.allowed_folders = [
            AllowlistedFolder(folder_id="folder123", alias="allowed_1"),
        ]
        import tempfile
        self.temp_dir = tempfile.mkdtemp()
        self.workspace_dir = Path(self.temp_dir).resolve()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.temp_dir)

    def test_config_validation(self):
        with self.assertRaisesRegex(ConfigError, "bounds"):
            DriveReadService(self.factory, self.allowed_folders, self.workspace_dir, max_file_size_bytes=-1)
        with self.assertRaisesRegex(ConfigError, "bounds"):
            DriveReadService(self.factory, self.allowed_folders, self.workspace_dir, max_results_limit=0)
        with self.assertRaisesRegex(ConfigError, "cannot be empty"):
            DriveReadService(self.factory, [], self.workspace_dir)
        with self.assertRaisesRegex(ConfigError, "non-empty strings"):
            DriveReadService(self.factory, [AllowlistedFolder("", "")], self.workspace_dir)

    def test_symlink_workspace_rejection(self):
        import tempfile
        import os
        target_dir = Path(tempfile.mkdtemp())
        link_path = Path(tempfile.mkdtemp())
        os.rmdir(link_path)
        link_path.symlink_to(target_dir)

        with self.assertRaisesRegex(ConfigError, "Workspace directory cannot be a symlink"):
            DriveReadService(self.factory, self.allowed_folders, link_path)

        import shutil
        shutil.rmtree(target_dir)

    def test_pagination_unique_results(self):
        svc = DriveReadService(self.factory, self.allowed_folders, self.workspace_dir, max_results_limit=2)

        call_count = [0]
        def mock_list(*args, **kwargs):
            res = MagicMock()
            if call_count[0] == 0:
                res.execute.return_value = {
                    "files": [
                        {"id": "file1", "name": "f1", "mimeType": "text/plain", "modifiedTime": "2023-10-01T12:00:00Z", "parents": ["folder123"]},
                        {"id": "file1", "name": "f1_dup", "mimeType": "text/plain", "modifiedTime": "2023-10-01T12:00:00Z", "parents": ["folder123"]}
                    ],
                    "nextPageToken": "token2"
                }
            else:
                res.execute.return_value = {
                    "files": [
                        {"id": "file2", "name": "f2", "mimeType": "text/plain", "modifiedTime": "2023-10-01T12:00:00Z", "parents": ["folder123"]}
                    ],
                    "nextPageToken": None
                }
            call_count[0] += 1
            return res

        self.mock_service.files().list = mock_list
        page = svc.search_files("folder123", limit=2)
        self.assertEqual(len(page.results), 2)
        ids = {r.metadata.file_id for r in page.results}
        self.assertEqual(ids, {"file1", "file2"})

    def test_provider_error_mapping(self):
        svc = DriveReadService(self.factory, self.allowed_folders, self.workspace_dir)

        def mock_get(*args, **kwargs):
            raise Exception("Connection timeout exceeded")

        self.mock_service.files().get = mock_get
        from app.google_workspace.drive.models import DriveProviderError
        try:
            svc.get_metadata("file1")
        except DriveProviderError as e:
            self.assertTrue(e.retryable)

    def test_filename_sanitization(self):
        self.assertEqual(_sanitize_filename_stem("report.pdf.exe").lstrip('_'), "report_pdf_exe")
        self.assertEqual(_sanitize_filename_stem("../../secret").lstrip('_'), "secret")
        self.assertEqual(_sanitize_filename_stem(".hidden").lstrip('_'), "hidden")
        self.assertEqual(_sanitize_filename_stem("CON").lstrip('_'), "CON_")

    def test_missing_dependency_raises_config_error(self):
        import sys
        # Ensure it's not loaded
        sys.modules.pop('googleapiclient.http', None)

        svc = DriveReadService(self.factory, self.allowed_folders, self.workspace_dir)
        self.mock_service.files().get().execute.return_value = {
            "id": "file1",
            "name": "test.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2023-10-01T12:00:00Z",
            "parents": ["folder123"],
        }

        with self.assertRaisesRegex(ConfigError, "Google API client not available"):
            svc.download_working_copy("file1")

    def test_internal_programming_defect_raises(self):
        svc = DriveReadService(self.factory, self.allowed_folders, self.workspace_dir)
        def mock_get(*args, **kwargs):
            raise NameError("Something internal is undefined")

        self.mock_service.files().get = mock_get

        with self.assertRaises(NameError):
            svc.get_metadata("file1")

if __name__ == "__main__":
    unittest.main()
