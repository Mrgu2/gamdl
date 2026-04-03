import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gamdl.desktop_runtime import detect_desktop_runtime
from gamdl.windows_app import WindowsLauncher, main


class DesktopRuntimeTests(unittest.TestCase):
    @patch("gamdl.desktop_runtime.platform.system", return_value="Windows")
    def test_windows_runtime_disables_native_login_and_folder_picker(self, _mock_system):
        runtime = detect_desktop_runtime(folder_picker_supported=False)

        self.assertEqual(runtime.platform, "Windows")
        self.assertFalse(runtime.native_login_supported)
        self.assertFalse(runtime.folder_picker_supported)
        self.assertTrue(runtime.file_actions_supported)
        self.assertIn("暂不支持一键拉起浏览器登录", runtime.native_login_message)
        self.assertIn("不支持文件夹选择器", runtime.output_path_message)

    @patch("gamdl.desktop_runtime.platform.system", return_value="Darwin")
    def test_macos_runtime_uses_browser_assisted_login_path(self, _mock_system):
        runtime = detect_desktop_runtime(folder_picker_supported=True)

        self.assertEqual(runtime.platform, "Darwin")
        self.assertTrue(runtime.native_login_supported)
        self.assertTrue(runtime.folder_picker_supported)
        self.assertTrue(runtime.file_actions_supported)
        self.assertIn("浏览器辅助登录是主路径", runtime.native_login_message)


class WindowsLauncherTests(unittest.TestCase):
    def test_launcher_opens_browser_and_shuts_down(self):
        fake_root = MagicMock()
        fake_root.winfo_exists.return_value = True
        tk_module = types.ModuleType("tkinter")
        ttk_module = types.ModuleType("tkinter.ttk")
        tk_module.Tk = MagicMock(return_value=fake_root)
        tk_module.ttk = ttk_module

        for name in ("Frame", "Label", "Button"):
            setattr(ttk_module, name, MagicMock())

        closed = []

        with (
            patch.dict(sys.modules, {"tkinter": tk_module, "tkinter.ttk": ttk_module}),
            patch("gamdl.windows_app.webbrowser.open") as open_browser,
        ):
            launcher = WindowsLauncher("http://127.0.0.1:8765", lambda: closed.append(True))
            launcher.open_browser()
            launcher.close()

        open_browser.assert_called_once_with("http://127.0.0.1:8765", new=1)
        fake_root.after.assert_called_once_with(0, fake_root.destroy)
        self.assertEqual(closed, [True])

    @patch("gamdl.windows_app.platform.system", return_value="Windows")
    def test_main_passes_network_mode_to_wrapper_preheat(self, _mock_system):
        args = SimpleNamespace(host="127.0.0.1", port=8765)
        settings = SimpleNamespace(
            log_level="INFO",
            use_wrapper=True,
            song_codec="alac",
            wrapper_decrypt_ip="127.0.0.1:10022",
            network_mode="direct",
            proxy_url="",
        )
        fake_server = MagicMock()
        fake_server.server_address = ("127.0.0.1", 8765)

        with (
            patch("gamdl.windows_app.argparse.ArgumentParser.parse_args", return_value=args),
            patch("gamdl.windows_app.AppSettingsStore") as settings_store_cls,
            patch("gamdl.windows_app.configure_app_logging"),
            patch("gamdl.windows_app.WrapperManager") as wrapper_manager_cls,
            patch("gamdl.windows_app._pick_available_port", return_value=8765),
            patch("gamdl.windows_app.create_server", return_value=fake_server),
            patch("gamdl.windows_app.threading.Thread") as thread_cls,
            patch("gamdl.windows_app.WindowsLauncher") as launcher_cls,
        ):
            settings_store = settings_store_cls.return_value
            settings_store.load.return_value = settings
            wrapper_manager = wrapper_manager_cls.return_value
            wrapper_manager.ensure_running.return_value = "127.0.0.1:10022"

            launcher = launcher_cls.return_value
            launcher.run.return_value = None
            thread_cls.return_value = MagicMock()

            main()

        wrapper_manager.ensure_running.assert_called_once()
        self.assertEqual(wrapper_manager.ensure_running.call_args.args[0], "127.0.0.1:10022")
        self.assertEqual(wrapper_manager.ensure_running.call_args.args[1].mode, "direct")


if __name__ == "__main__":
    unittest.main()
