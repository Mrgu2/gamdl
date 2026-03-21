import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from gamdl.app.settings import AppSettings
from gamdl.interface import SongCodec


class WrapperPreheatTests(unittest.TestCase):
    def _desktop_preheat_call_count(self, codec: str) -> int:
        from gamdl.desktop_app import main

        settings = AppSettings(
            output_path="/tmp",
            use_wrapper=True,
            song_codec=codec,
            wrapper_decrypt_ip="127.0.0.1:10022",
        )
        settings_store = MagicMock()
        settings_store.load.return_value = settings
        server = MagicMock()
        server.server_address = ("127.0.0.1", 8765)
        wrapper = MagicMock()
        webview = types.SimpleNamespace(
            create_window=MagicMock(return_value=object()),
            start=MagicMock(),
        )
        args = types.SimpleNamespace(host="127.0.0.1", port=8765)

        with (
            patch.dict(sys.modules, {"webview": webview}),
            patch("gamdl.desktop_app.argparse.ArgumentParser.parse_args", return_value=args),
            patch("gamdl.desktop_app.AppPaths", return_value=MagicMock()),
            patch("gamdl.desktop_app.AppSettingsStore", return_value=settings_store),
            patch("gamdl.desktop_app.AppLogStore", return_value=MagicMock()),
            patch("gamdl.desktop_app.configure_app_logging"),
            patch("gamdl.desktop_app.WrapperManager", return_value=wrapper),
            patch("gamdl.desktop_app._pick_available_port", return_value=8765),
            patch("gamdl.desktop_app.create_server", return_value=server),
            patch("gamdl.desktop_app.threading.Thread", return_value=MagicMock()),
        ):
            main()

        return wrapper.ensure_running.call_count

    def _windows_preheat_call_count(self, codec: str) -> int:
        from gamdl.windows_app import main

        settings = AppSettings(
            output_path="/tmp",
            use_wrapper=True,
            song_codec=codec,
            wrapper_decrypt_ip="127.0.0.1:10022",
        )
        settings_store = MagicMock()
        settings_store.load.return_value = settings
        server = MagicMock()
        server.server_address = ("127.0.0.1", 8765)
        wrapper = MagicMock()
        args = types.SimpleNamespace(host="127.0.0.1", port=8765)
        launcher = MagicMock()
        launcher.run.return_value = None

        with (
            patch("gamdl.windows_app.platform.system", return_value="Windows"),
            patch("gamdl.windows_app.argparse.ArgumentParser.parse_args", return_value=args),
            patch("gamdl.windows_app.AppPaths", return_value=MagicMock()),
            patch("gamdl.windows_app.AppSettingsStore", return_value=settings_store),
            patch("gamdl.windows_app.AppLogStore", return_value=MagicMock()),
            patch("gamdl.windows_app.configure_app_logging"),
            patch("gamdl.windows_app.WrapperManager", return_value=wrapper),
            patch("gamdl.windows_app._pick_available_port", return_value=8765),
            patch("gamdl.windows_app.create_server", return_value=server),
            patch("gamdl.windows_app.threading.Thread", return_value=MagicMock()),
            patch("gamdl.windows_app.WindowsLauncher", return_value=launcher),
        ):
            main()

        return wrapper.ensure_running.call_count

    def test_alac_preheats_wrapper(self):
        for get_call_count in (self._desktop_preheat_call_count, self._windows_preheat_call_count):
            with self.subTest(target=get_call_count.__name__):
                self.assertEqual(get_call_count(SongCodec.ALAC.value), 1)

    def test_atmos_preheats_wrapper(self):
        for get_call_count in (self._desktop_preheat_call_count, self._windows_preheat_call_count):
            with self.subTest(target=get_call_count.__name__):
                self.assertEqual(get_call_count(SongCodec.ATMOS.value), 1)

    def test_aac_does_not_preheat_wrapper(self):
        for get_call_count in (self._desktop_preheat_call_count, self._windows_preheat_call_count):
            with self.subTest(target=get_call_count.__name__):
                self.assertEqual(get_call_count(SongCodec.AAC.value), 0)


if __name__ == "__main__":
    unittest.main()
