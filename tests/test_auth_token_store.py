import tempfile
import unittest
from pathlib import Path

from gamdl.app.auth import (
    KEYRING_DELETE_UNAVAILABLE_MESSAGE,
    KEYRING_UNAVAILABLE_MESSAGE,
    TokenDeletionError,
    TokenPersistenceError,
    TokenStore,
)
from gamdl.app.paths import AppPaths


class _BrokenKeyring:
    def __init__(
        self,
        *,
        get_error: Exception | None = None,
        set_error: Exception | None = None,
        delete_error: Exception | None = None,
    ) -> None:
        self.get_error = get_error
        self.set_error = set_error
        self.delete_error = delete_error

    def get_password(self, service: str, username: str) -> str | None:
        if self.get_error is not None:
            raise self.get_error
        return None

    def set_password(self, service: str, username: str, token: str) -> None:
        if self.set_error is not None:
            raise self.set_error

    def delete_password(self, service: str, username: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error


class PasswordDeleteError(RuntimeError):
    pass


class _WorkingKeyring:
    def __init__(self) -> None:
        self.values = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, token: str) -> None:
        self.values[(service, username)] = token

    def delete_password(self, service: str, username: str) -> None:
        self.values.pop((service, username), None)


class TokenStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.store = TokenStore(self.paths)

    def test_get_removes_legacy_fallback_file_when_present(self):
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("fallback-token", encoding="utf-8")
        self.store._keyring = lambda: _WorkingKeyring()  # type: ignore[method-assign]

        token = self.store.get()

        self.assertIsNone(token)
        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_set_raises_when_keyring_set_fails(self):
        self.store._keyring = lambda: _BrokenKeyring(set_error=RuntimeError("boom-set"))  # type: ignore[method-assign]

        with self.assertRaisesRegex(TokenPersistenceError, KEYRING_UNAVAILABLE_MESSAGE):
            self.store.set("written-token")

        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_set_keeps_token_out_of_fallback_file_when_keyring_write_succeeds(self):
        working_keyring = _WorkingKeyring()
        self.store._keyring = lambda: working_keyring  # type: ignore[method-assign]

        self.store.set("written-token")
        self.assertFalse(self.paths.token_fallback_path.exists())

        token = self.store.get()

        self.assertEqual(token, "written-token")
        self.assertEqual(
            working_keyring.get_password("gamdl.desktop", "media-user-token"),
            "written-token",
        )
        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_get_returns_cached_token_when_keyring_read_temporarily_fails(self):
        class _ReadFailKeyring(_WorkingKeyring):
            def get_password(self, service: str, username: str) -> str | None:
                raise RuntimeError("boom-read")

        working_keyring = _WorkingKeyring()
        self.store._keyring = lambda: working_keyring  # type: ignore[method-assign]
        self.store.set("written-token")
        self.store._keyring = lambda: _ReadFailKeyring()  # type: ignore[method-assign]

        token = self.store.get()

        self.assertEqual(token, "written-token")
        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_get_returns_none_after_restart_when_keyring_read_fails(self):
        class _ReadFailKeyring(_WorkingKeyring):
            def get_password(self, service: str, username: str) -> str | None:
                raise RuntimeError("boom-read")

        working_keyring = _WorkingKeyring()
        self.store._keyring = lambda: working_keyring  # type: ignore[method-assign]
        self.store.set("written-token")

        restarted_store = TokenStore(self.paths)
        restarted_store._keyring = lambda: _ReadFailKeyring()  # type: ignore[method-assign]

        token = restarted_store.get()

        self.assertIsNone(token)
        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_get_keeps_legacy_fallback_file_when_keyring_read_fails(self):
        class _ReadFailKeyring(_WorkingKeyring):
            def get_password(self, service: str, username: str) -> str | None:
                raise RuntimeError("boom-read")

        self.paths.ensure()
        self.paths.token_fallback_path.write_text("fallback-token", encoding="utf-8")
        self.store._keyring = lambda: _ReadFailKeyring()  # type: ignore[method-assign]

        token = self.store.get()

        self.assertIsNone(token)
        self.assertTrue(self.paths.token_fallback_path.exists())

    def test_set_raises_when_keyring_is_unavailable(self):
        self.store._keyring = lambda: None  # type: ignore[method-assign]

        with self.assertRaisesRegex(TokenPersistenceError, KEYRING_UNAVAILABLE_MESSAGE):
            self.store.set("written-token")

        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_delete_raises_when_keyring_delete_fails(self):
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("fallback-token", encoding="utf-8")
        self.store._keyring = lambda: _BrokenKeyring(  # type: ignore[method-assign]
            get_error=RuntimeError("boom-read"),
            delete_error=RuntimeError("boom-del"),
        )

        with self.assertRaisesRegex(TokenDeletionError, KEYRING_DELETE_UNAVAILABLE_MESSAGE):
            self.store.delete()

        self.assertFalse(self.paths.token_fallback_path.exists())

    def test_delete_failure_clears_cached_token_for_current_process(self):
        working_keyring = _WorkingKeyring()
        self.store._keyring = lambda: working_keyring  # type: ignore[method-assign]
        self.store.set("written-token")
        self.store._keyring = lambda: _BrokenKeyring(  # type: ignore[method-assign]
            get_error=RuntimeError("boom-read"),
            delete_error=RuntimeError("boom-del"),
        )

        with self.assertRaisesRegex(TokenDeletionError, KEYRING_DELETE_UNAVAILABLE_MESSAGE):
            self.store.delete()

        self.assertIsNone(self.store._cached_token)
        self.assertIsNone(self.store.get())

    def test_delete_treats_missing_keyring_entry_as_already_logged_out(self):
        class _MissingOnDeleteKeyring(_WorkingKeyring):
            def delete_password(self, service: str, username: str) -> None:
                raise RuntimeError("missing")

            def get_password(self, service: str, username: str) -> str | None:
                return None

        self.store._keyring = lambda: _MissingOnDeleteKeyring()  # type: ignore[method-assign]

        self.store.delete()

    def test_delete_raises_when_keyring_is_unavailable(self):
        self.store._keyring = lambda: None  # type: ignore[method-assign]

        with self.assertRaisesRegex(TokenDeletionError, KEYRING_DELETE_UNAVAILABLE_MESSAGE):
            self.store.delete()

    def test_keyring_available_false_when_keyring_missing(self):
        self.store._keyring = lambda: None  # type: ignore[method-assign]

        self.assertFalse(self.store.keyring_available())

    def test_delete_treats_missing_keyring_entry_as_already_cleared(self):
        self.store._keyring = lambda: _BrokenKeyring(delete_error=PasswordDeleteError("missing"))  # type: ignore[method-assign]

        self.store.delete()


if __name__ == "__main__":
    unittest.main()
