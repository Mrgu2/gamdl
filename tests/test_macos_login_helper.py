import json
import unittest
from pathlib import Path
from subprocess import CompletedProcess
from unittest.mock import patch

from gamdl.macos_login_helper import (
    build_login_helper_command,
    capture_media_user_token_isolated,
)


class MacosLoginHelperTests(unittest.TestCase):
    def test_build_login_helper_command_uses_module_mode_when_not_frozen(self):
        with patch("gamdl.macos_login_helper.sys.executable", "/tmp/python3"), patch(
            "gamdl.macos_login_helper.sys.frozen",
            False,
            create=True,
        ):
            command = build_login_helper_command(language="en-US", timeout=42)

        self.assertEqual(
            command,
            [
                "/tmp/python3",
                "-m",
                "gamdl.macos_login_helper",
                "--language",
                "en-US",
                "--timeout",
                "42",
            ],
        )

    def test_build_login_helper_command_uses_current_executable_when_frozen(self):
        with patch("gamdl.macos_login_helper.sys.executable", "/Applications/Apple Music Downloader.app/Contents/MacOS/Apple Music Downloader"), patch(
            "gamdl.macos_login_helper.sys.frozen",
            True,
            create=True,
        ):
            command = build_login_helper_command(language="zh-CN", timeout=9)

        self.assertEqual(
            command,
            [
                "open",
                "-n",
                "-W",
                "-a",
                "/Applications/Apple Music Downloader.app/Contents/Helpers/Apple Music Login Helper.app",
                "--args",
                "--language",
                "zh-CN",
                "--timeout",
                "9",
            ],
        )

    def test_capture_media_user_token_isolated_reads_helper_output(self):
        def fake_run(command, capture_output, text, check, cwd):
            output_flag = command.index("--output")
            output_path = Path(command[output_flag + 1])
            output_path.write_text(
                json.dumps({"media_user_token": "abc", "captured_at": 2.0}),
                encoding="utf-8",
            )
            return CompletedProcess(command, 0, stdout="", stderr="")

        with patch("gamdl.macos_login_helper.subprocess.run", side_effect=fake_run):
            payload = capture_media_user_token_isolated(language="zh-CN", timeout=3)

        self.assertEqual(payload["media_user_token"], "abc")
        self.assertEqual(payload["captured_at"], 2.0)

    def test_capture_media_user_token_isolated_surfaces_helper_error(self):
        with patch(
            "gamdl.macos_login_helper.subprocess.run",
            return_value=CompletedProcess(["helper"], 1, stdout="", stderr="helper failed"),
        ):
            with self.assertRaisesRegex(RuntimeError, "helper failed"):
                capture_media_user_token_isolated()

    def test_capture_media_user_token_isolated_prefers_helper_payload_error(self):
        def fake_run(command, capture_output, text, check, cwd):
            output_flag = command.index("--output")
            output_path = Path(command[output_flag + 1])
            output_path.write_text(
                json.dumps({"error": "token capture failed"}),
                encoding="utf-8",
            )
            return CompletedProcess(command, 1, stdout="", stderr="")

        with patch("gamdl.macos_login_helper.subprocess.run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "token capture failed"):
                capture_media_user_token_isolated()


if __name__ == "__main__":
    unittest.main()
