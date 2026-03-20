import socket
import tempfile
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from gamdl.app import DesktopFileActions
from gamdl.desktop_app import _pick_available_port, _select_file, _select_folder, _select_input_folder


class DesktopAppTests(unittest.TestCase):
    def test_pick_available_port_keeps_preferred_port_when_free(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            preferred_port = int(probe.getsockname()[1])

        selected_port = _pick_available_port("127.0.0.1", preferred_port)
        self.assertEqual(selected_port, preferred_port)

    def test_pick_available_port_falls_back_when_preferred_port_is_busy(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind(("127.0.0.1", 0))
            probe.listen(1)
            busy_port = int(probe.getsockname()[1])

            selected_port = _pick_available_port("127.0.0.1", busy_port)
            self.assertNotEqual(selected_port, busy_port)
            self.assertGreater(selected_port, 0)

    def test_select_folder_returns_selected_path(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="/tmp/chosen\n", stderr=""),
        ):
            self.assertEqual(_select_folder(), "/tmp/chosen")

    def test_select_folder_returns_none_on_cancel(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="\n", stderr=""),
        ):
            self.assertIsNone(_select_folder())

    def test_select_file_returns_selected_path(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="/tmp/input.m4a\n", stderr=""),
        ):
            self.assertEqual(_select_file(), "/tmp/input.m4a")

    def test_select_input_folder_returns_selected_path(self):
        with patch(
            "gamdl.desktop_app.subprocess.run",
            return_value=CompletedProcess(args=["osascript"], returncode=0, stdout="/tmp/input-folder\n", stderr=""),
        ):
            self.assertEqual(_select_input_folder(), "/tmp/input-folder")

    def test_file_actions_use_open_on_macos(self):
        file_actions = DesktopFileActions(current_platform="Darwin")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target))
                file_actions.reveal_file(str(target))
                run_mock.assert_any_call(["open", str(target.resolve())], check=True)
                run_mock.assert_any_call(["open", "-R", str(target.resolve())], check=True)

    def test_file_actions_use_custom_application_on_macos(self):
        file_actions = DesktopFileActions(current_platform="Darwin")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target), application="VLC")
                run_mock.assert_called_once_with(
                    ["open", "-a", "VLC", str(target.resolve())],
                    check=True,
                )

    def test_file_actions_build_macos_open_with_options(self):
        file_actions = DesktopFileActions(current_platform="Darwin")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch.object(
                file_actions,
                "_mac_lookup_applications",
                return_value=(
                    {"label": "Music", "application_path": "/Applications/Music.app"},
                    [
                        {"label": "VLC", "application_path": "/Applications/VLC.app"},
                        {"label": "Music", "application_path": "/Applications/Music.app"},
                        {"label": "IINA", "application_path": "/Applications/IINA.app"},
                    ],
                ),
            ):
                options = file_actions.get_open_with_options(str(target))

        self.assertEqual(options[0]["label"], "Music")
        self.assertTrue(options[0]["is_default"])
        self.assertEqual(options[1]["kind"], "separator")
        self.assertEqual(options[2]["label"], "IINA")
        self.assertEqual(options[3]["label"], "VLC")
        self.assertEqual(options[-1]["kind"], "pick-application")

    def test_file_actions_use_windows_shell_mappings(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with (
                patch("gamdl.app.file_actions.os.startfile", create=True) as startfile_mock,
                patch("gamdl.app.file_actions.subprocess.run") as run_mock,
            ):
                file_actions.open_file(str(target))
                file_actions.reveal_file(str(target))
                startfile_mock.assert_called_once_with(str(target.resolve()))
                run_mock.assert_called_once_with(["explorer", f"/select,{target.resolve()}"], check=True)

    def test_file_actions_use_custom_application_on_windows(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target), application="C:/Program Files/VLC/vlc.exe")
                run_mock.assert_called_once_with(
                    ["C:/Program Files/VLC/vlc.exe", str(target.resolve())],
                    check=True,
                )

    def test_file_actions_build_windows_open_with_options(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            options = file_actions.get_open_with_options(
                str(target),
                configured_application="C:/Program Files/VLC/vlc.exe",
            )

        self.assertEqual(options[0]["kind"], "default")
        self.assertEqual(options[1]["kind"], "separator")
        self.assertEqual(options[2]["application_path"], "C:/Program Files/VLC/vlc.exe")

    def test_file_actions_reject_unsupported_platform(self):
        file_actions = DesktopFileActions(current_platform="Linux")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "暂不支持桌面文件操作"):
                file_actions.open_file(str(target))


if __name__ == "__main__":
    unittest.main()
