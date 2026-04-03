import tempfile
import unittest
from pathlib import Path

from gamdl.app.auth import TokenStore
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

    def test_get_reads_fallback_file_when_keyring_get_raises(self):
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("fallback-token", encoding="utf-8")
        self.store._keyring = lambda: _BrokenKeyring(get_error=RuntimeError("boom-get"))  # type: ignore[method-assign]

        token = self.store.get()

        self.assertEqual(token, "fallback-token")

    def test_set_writes_fallback_file_when_keyring_set_raises(self):
        self.store._keyring = lambda: _BrokenKeyring(set_error=RuntimeError("boom-set"))  # type: ignore[method-assign]

        self.store.set("written-token")

        self.assertEqual(
            self.paths.token_fallback_path.read_text(encoding="utf-8"),
            "written-token",
        )

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

    def test_set_removes_stale_fallback_file_when_keyring_write_succeeds(self):
        working_keyring = _WorkingKeyring()
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("stale-token", encoding="utf-8")
        self.store._keyring = lambda: working_keyring  # type: ignore[method-assign]

        self.store.set("written-token")

        self.assertFalse(self.paths.token_fallback_path.exists())
        self.assertEqual(
            working_keyring.get_password("gamdl.desktop", "media-user-token"),
            "written-token",
        )

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

    def test_get_returns_none_after_restart_when_keyring_read_fails_without_fallback(self):
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

    def test_set_writes_fallback_file_when_keyring_is_unavailable(self):
        self.store._keyring = lambda: None  # type: ignore[method-assign]

        self.store.set("written-token")

        self.assertEqual(
            self.paths.token_fallback_path.read_text(encoding="utf-8"),
            "written-token",
        )

    def test_delete_removes_fallback_file_when_keyring_delete_raises(self):
        self.paths.ensure()
        self.paths.token_fallback_path.write_text("fallback-token", encoding="utf-8")
        self.store._keyring = lambda: _BrokenKeyring(delete_error=RuntimeError("boom-del"))  # type: ignore[method-assign]

        self.store.delete()

        self.assertFalse(self.paths.token_fallback_path.exists())
        self.assertIsNone(self.store.get())


if __name__ == "__main__":
    unittest.main()
