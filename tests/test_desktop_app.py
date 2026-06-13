import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from gamdl.app import DesktopFileActions
from gamdl.app.file_actions import MAC_OPEN_BIN
from gamdl.desktop_app import _pick_available_port, _select_file, _select_folder, _select_input_folder, main


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

    def test_main_runs_macos_login_helper_mode_without_starting_ui(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "login.json"
            args = SimpleNamespace(
                macos_login_helper=True,
                macos_login_helper_output=str(output_path),
                language="en-US",
                timeout=12,
            )
            with (
                patch("gamdl.desktop_app.argparse.ArgumentParser.parse_args", return_value=args),
                patch(
                    "gamdl.desktop_app.capture_media_user_token",
                    return_value={"media_user_token": "token-123", "captured_at": 1.25},
                ) as helper_mock,
                patch("gamdl.desktop_app.create_server", new=MagicMock()) as create_server_mock,
                ):
                main()

            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                '{"media_user_token": "token-123", "captured_at": 1.25}',
            )

        helper_mock.assert_called_once_with(language="en-US", timeout=12)
        create_server_mock.assert_not_called()

    def test_main_writes_helper_error_payload_before_exiting(self):
        with tempfile.TemporaryDirectory() as tempdir:
            output_path = Path(tempdir) / "login-error.json"
            args = SimpleNamespace(
                macos_login_helper=True,
                macos_login_helper_output=str(output_path),
                language="zh-CN",
                timeout=5,
            )
            with (
                patch("gamdl.desktop_app.argparse.ArgumentParser.parse_args", return_value=args),
                patch(
                    "gamdl.desktop_app.capture_media_user_token",
                    side_effect=RuntimeError("inner helper failed"),
                ),
            ):
                with self.assertRaises(SystemExit) as exc:
                    main()

            self.assertEqual(exc.exception.code, 1)
            self.assertEqual(
                output_path.read_text(encoding="utf-8"),
                '{"error": "inner helper failed"}',
            )

    def test_main_joins_server_thread_after_webview_exits(self):
        args = SimpleNamespace(
            macos_login_helper=False,
            host="127.0.0.1",
            port=8765,
        )
        settings = SimpleNamespace(
            log_level="INFO",
            use_wrapper=False,
            song_codec="aac-legacy",
            wrapper_decrypt_ip="127.0.0.1:10022",
            network_mode="auto",
            proxy_url="",
        )
        fake_server = MagicMock()
        fake_server.local_url.return_value = "http://127.0.0.1:8765/session/"
        fake_thread = MagicMock()
        fake_webview = types.SimpleNamespace(
            create_window=MagicMock(),
            start=MagicMock(),
        )

        with (
            patch("gamdl.desktop_app.argparse.ArgumentParser.parse_args", return_value=args),
            patch("gamdl.desktop_app.AppSettingsStore") as settings_store_cls,
            patch("gamdl.desktop_app.AppLogStore", return_value=MagicMock()),
            patch("gamdl.desktop_app.configure_app_logging"),
            patch("gamdl.desktop_app._pick_available_port", return_value=8765),
            patch("gamdl.desktop_app.create_server", return_value=fake_server),
            patch("gamdl.desktop_app.threading.Thread", return_value=fake_thread),
            patch.dict(sys.modules, {"webview": fake_webview}),
        ):
            settings_store_cls.return_value.load.return_value = settings
            main()

        fake_thread.start.assert_called_once()
        fake_server.shutdown.assert_called_once()
        fake_server.server_close.assert_called_once()
        fake_thread.join.assert_called_once_with(timeout=2)

    def test_file_actions_use_open_on_macos(self):
        file_actions = DesktopFileActions(current_platform="Darwin")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target))
                file_actions.reveal_file(str(target))
                run_mock.assert_any_call([MAC_OPEN_BIN, str(target.resolve())], check=True)
                run_mock.assert_any_call([MAC_OPEN_BIN, "-R", str(target.resolve())], check=True)

    def test_file_actions_use_custom_application_on_macos(self):
        file_actions = DesktopFileActions(current_platform="Darwin")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target), application="VLC")
                run_mock.assert_called_once_with(
                    [MAC_OPEN_BIN, "-a", "VLC", str(target.resolve())],
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
            application = Path(tempdir) / "vlc.exe"
            target.write_text("audio", encoding="utf-8")
            application.write_text("exe", encoding="utf-8")
            with patch("gamdl.app.file_actions.subprocess.run") as run_mock:
                file_actions.open_file(str(target), application=str(application))
                run_mock.assert_called_once_with(
                    [str(application.resolve()), str(target.resolve())],
                    check=True,
                )

    def test_file_actions_reject_relative_custom_application_on_windows(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "绝对文件路径"):
                file_actions.open_file(str(target), application="vlc.exe")

    def test_file_actions_build_windows_open_with_options(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            application = Path(tempdir) / "vlc.exe"
            target.write_text("audio", encoding="utf-8")
            application.write_text("exe", encoding="utf-8")
            options = file_actions.get_open_with_options(
                str(target),
                configured_application=str(application),
            )

        self.assertEqual(options[0]["kind"], "default")
        self.assertEqual(options[1]["kind"], "separator")
        self.assertEqual(options[2]["application_path"], str(application.resolve()))

    def test_file_actions_omit_invalid_windows_open_with_option(self):
        file_actions = DesktopFileActions(current_platform="Windows")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            options = file_actions.get_open_with_options(
                str(target),
                configured_application="vlc.exe",
            )

        self.assertEqual(options, [
            {
                "label": "系统默认应用",
                "kind": "default",
                "is_default": True,
                "application_path": None,
            }
        ])

    def test_file_actions_reject_unsupported_platform(self):
        file_actions = DesktopFileActions(current_platform="Linux")
        with tempfile.TemporaryDirectory() as tempdir:
            target = Path(tempdir) / "song.m4a"
            target.write_text("audio", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "暂不支持桌面文件操作"):
                file_actions.open_file(str(target))


if __name__ == "__main__":
    unittest.main()
