import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamdl.app.auth import AuthManager, LoginMethod, SessionStatus
from gamdl.app.paths import AppPaths


class AuthSessionStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.manager = AuthManager(self.paths)
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("test-token", encoding="utf-8")

    def _write_session(self, payload: dict) -> None:
        self.paths.session_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    def test_verify_status_falls_back_when_login_method_is_null(self):
        self._write_session({"login_method": None})

        with patch.object(
            self.manager,
            "_verify_token",
            return_value=SessionStatus(connected=True, storefront="cn"),
        ):
            status = self.manager.get_session_status(verify=True)

        self.assertTrue(status.connected)
        self.assertEqual(status.login_method, LoginMethod.WEBVIEW.value)

    def test_verify_status_inferrs_browser_import_when_login_method_missing(self):
        self._write_session({"browser": "chrome"})

        with patch.object(
            self.manager,
            "_verify_token",
            return_value=SessionStatus(connected=True, storefront="cn"),
        ):
            status = self.manager.get_session_status(verify=True)

        self.assertTrue(status.connected)
        self.assertEqual(status.login_method, LoginMethod.BROWSER_IMPORT.value)
        self.assertEqual(status.browser, "chrome")

    def test_verify_status_falls_back_when_login_method_is_dirty(self):
        self._write_session({"login_method": "legacy-login"})

        with patch.object(
            self.manager,
            "_verify_token",
            return_value=SessionStatus(connected=True, storefront="cn"),
        ):
            status = self.manager.get_session_status(verify=True)

        self.assertTrue(status.connected)
        self.assertEqual(status.login_method, LoginMethod.WEBVIEW.value)


if __name__ == "__main__":
    unittest.main()
