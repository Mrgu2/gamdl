import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamdl.app import AppPaths, AppSettingsStore


class AppSettingsOutputPathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.store = AppSettingsStore(self.paths)

    def test_validate_output_path_rejects_empty_string(self):
        with self.assertRaisesRegex(ValueError, "请先选择下载目录"):
            self.store.validate_output_path("")

    def test_validate_output_path_rejects_whitespace_string(self):
        with self.assertRaisesRegex(ValueError, "请先选择下载目录"):
            self.store.validate_output_path("   \t   ")

    def test_validate_output_path_creates_directory_and_returns_absolute_path(self):
        target = Path("downloads") / "nested"
        previous_cwd = Path.cwd()
        os.chdir(self.tempdir.name)
        try:
            normalized = self.store.validate_output_path(str(target))
        finally:
            os.chdir(previous_cwd)

        normalized_path = Path(normalized)
        expected_path = Path(self.tempdir.name) / target
        self.assertTrue(normalized_path.is_absolute())
        self.assertEqual(normalized_path.resolve(), expected_path.resolve())
        self.assertTrue(expected_path.is_dir())

    def test_validate_output_path_rejects_unwritable_directory(self):
        target = Path(self.tempdir.name) / "downloads"
        target.mkdir(parents=True, exist_ok=True)

        with patch("pathlib.Path.write_text", side_effect=OSError("denied")):
            with self.assertRaisesRegex(ValueError, "没有写权限"):
                self.store.validate_output_path(str(target))


if __name__ == "__main__":
    unittest.main()
