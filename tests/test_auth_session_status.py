import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from gamdl.app.auth import (
    BrowserType,
    KEYRING_DELETE_UNAVAILABLE_MESSAGE,
    KEYRING_UNAVAILABLE_MESSAGE,
    AuthManager,
    LoginMethod,
    SessionStatus,
    TokenDeletionError,
    TokenPersistenceError,
)
from gamdl.app.paths import AppPaths


class AuthSessionStatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.manager = AuthManager(self.paths)
        self.paths.ensure()
        self.keyring_values = {("gamdl.desktop", "media-user-token"): "test-token"}
        self.manager.token_store._keyring = lambda: self  # type: ignore[method-assign]

    def get_password(self, service: str, username: str) -> str | None:
        return self.keyring_values.get((service, username))

    def set_password(self, service: str, username: str, token: str) -> None:
        self.keyring_values[(service, username)] = token

    def delete_password(self, service: str, username: str) -> None:
        self.keyring_values.pop((service, username), None)

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

    def test_login_with_webview_uses_browser_assisted_flow(self):
        with (
            patch("gamdl.app.auth.platform.system", return_value="Darwin"),
            patch.object(self.manager, "_open_apple_music_login_in_browser") as open_browser,
            patch.object(
                self.manager,
                "_wait_for_browser_login",
                return_value=SessionStatus(
                    connected=True,
                    login_method=LoginMethod.BROWSER_IMPORT.value,
                    browser="chrome",
                    storefront="us",
                    language="zh-CN",
                ),
            ) as wait_login,
        ):
            status = self.manager.login_with_webview()

        self.assertTrue(status.connected)
        self.assertEqual(status.login_method, LoginMethod.BROWSER_IMPORT.value)
        self.assertEqual(status.browser, "chrome")
        self.assertEqual(status.storefront, "us")
        open_browser.assert_called_once()
        wait_login.assert_called_once()

    def test_invalidate_session_clears_token_and_preserves_context(self):
        self._write_session(
            {
                "connected": True,
                "login_method": LoginMethod.BROWSER_IMPORT.value,
                "browser": "chrome",
                "storefront": "us",
                "language": "zh-CN",
                "active_subscription": True,
                "account_restrictions": {},
            }
        )

        status = self.manager.invalidate_session("Apple Music 登录已失效，请重新登录。")

        self.assertFalse(status.connected)
        self.assertEqual(status.login_method, LoginMethod.BROWSER_IMPORT.value)
        self.assertEqual(status.browser, "chrome")
        self.assertEqual(status.storefront, "us")
        self.assertEqual(status.last_error, "Apple Music 登录已失效，请重新登录。")
        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_invalidate_session_still_marks_session_disconnected_when_token_delete_fails(self):
        self._write_session(
            {
                "connected": True,
                "login_method": LoginMethod.BROWSER_IMPORT.value,
                "browser": "chrome",
                "storefront": "us",
            }
        )

        with patch.object(
            self.manager.token_store,
            "delete",
            side_effect=TokenDeletionError(KEYRING_DELETE_UNAVAILABLE_MESSAGE),
        ):
            with self.assertRaisesRegex(TokenDeletionError, KEYRING_DELETE_UNAVAILABLE_MESSAGE):
                self.manager.invalidate_session("Apple Music 登录已失效，请重新登录。")

        saved = json.loads(self.paths.session_path.read_text(encoding="utf-8"))
        self.assertFalse(saved["connected"])
        self.assertEqual(saved["login_method"], LoginMethod.BROWSER_IMPORT.value)
        self.assertEqual(saved["last_error"], "Apple Music 登录已失效，请重新登录。")

    def test_logout_clears_session_file_even_when_token_delete_fails(self):
        self._write_session(
            {
                "connected": True,
                "login_method": LoginMethod.BROWSER_IMPORT.value,
                "browser": "chrome",
            }
        )

        with patch.object(
            self.manager.token_store,
            "delete",
            side_effect=TokenDeletionError(KEYRING_DELETE_UNAVAILABLE_MESSAGE),
        ):
            with self.assertRaisesRegex(TokenDeletionError, KEYRING_DELETE_UNAVAILABLE_MESSAGE):
                self.manager.logout()

        self.assertFalse(self.paths.session_path.exists())

    def test_get_session_status_surfaces_keyring_unavailable_when_not_logged_in(self):
        self.keyring_values.clear()
        self.manager.token_store._keyring = lambda: None  # type: ignore[method-assign]

        status = self.manager.get_session_status(verify=False)

        self.assertFalse(status.connected)
        self.assertEqual(status.last_error, KEYRING_UNAVAILABLE_MESSAGE)

    def test_logout_clears_session_when_keyring_entry_is_already_missing(self):
        class PasswordDeleteError(RuntimeError):
            pass

        self._write_session({"connected": True, "storefront": "us"})

        class _MissingEntryKeyring:
            def get_password(self, service: str, username: str) -> str | None:
                return None

            def set_password(self, service: str, username: str, token: str) -> None:
                return None

            def delete_password(self, service: str, username: str) -> None:
                raise PasswordDeleteError("missing")

        self.manager.token_store._keyring = lambda: _MissingEntryKeyring()  # type: ignore[method-assign]

        status = self.manager.logout()

        self.assertFalse(status.connected)
        self.assertFalse(self.paths.session_path.exists())

    def test_import_from_browser_raises_when_token_cannot_be_persisted(self):
        with (
            patch.object(self.manager, "_load_browser_cookies", return_value=[object()]),
            patch.object(self.manager, "_extract_token_from_cookie_jar", return_value="token-123"),
            patch.object(
                self.manager,
                "_verify_token",
                return_value=SessionStatus(connected=True, storefront="cn"),
            ),
            patch.object(
                self.manager.token_store,
                "set",
                side_effect=TokenPersistenceError(KEYRING_UNAVAILABLE_MESSAGE),
            ),
        ):
            with self.assertRaisesRegex(TokenPersistenceError, KEYRING_UNAVAILABLE_MESSAGE):
                self.manager.import_from_browser(browser=BrowserType.CHROME)


if __name__ == "__main__":
    unittest.main()
