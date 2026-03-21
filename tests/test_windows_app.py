import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from gamdl.desktop_runtime import detect_desktop_runtime
from gamdl.windows_app import WindowsLauncher


class DesktopRuntimeTests(unittest.TestCase):
    @patch("gamdl.desktop_runtime.platform.system", return_value="Windows")
    def test_windows_runtime_disables_native_login_and_folder_picker(self, _mock_system):
        runtime = detect_desktop_runtime(folder_picker_supported=False)

        self.assertEqual(runtime.platform, "Windows")
        self.assertFalse(runtime.native_login_supported)
        self.assertFalse(runtime.folder_picker_supported)
        self.assertTrue(runtime.file_actions_supported)
        self.assertIn("不支持内置登录", runtime.native_login_message)
        self.assertIn("不支持文件夹选择器", runtime.output_path_message)

    @patch("gamdl.desktop_runtime.platform.system", return_value="Darwin")
    def test_macos_runtime_keeps_native_login_path(self, _mock_system):
        runtime = detect_desktop_runtime(folder_picker_supported=True)

        self.assertEqual(runtime.platform, "Darwin")
        self.assertTrue(runtime.native_login_supported)
        self.assertTrue(runtime.folder_picker_supported)
        self.assertTrue(runtime.file_actions_supported)
        self.assertIn("内置登录是主路径", runtime.native_login_message)


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


if __name__ == "__main__":
    unittest.main()
