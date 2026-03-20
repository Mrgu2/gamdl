import json
import asyncio
import platform
import tempfile
import threading
import time
import unittest
import urllib.request
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gamdl.app import AppPaths, AppSettingsStore, DownloadJob, DownloadService, SessionStatus
from gamdl.web_gui import (
    INDEX_HTML,
    GUI_SETTINGS_DEFAULTS,
    JobManager,
    WebGuiHandler,
    WebGuiServer,
    build_download_command,
    classify_url,
    parse_url_input,
)


class FakeAuthManager:
    def __init__(self) -> None:
        self.session = SessionStatus(connected=False, last_error="尚未登录")

    def get_session_status(self, verify: bool = False):
        return self.session

    def import_from_browser(self, browser, language: str = "zh-CN"):
        self.session = SessionStatus(
            connected=True,
            login_method="browser-import",
            browser=browser.value,
            storefront="us",
            language=language,
            active_subscription=True,
        )
        return self.session

    def login_with_webview(self):
        self.session = SessionStatus(
            connected=True,
            login_method="webview",
            storefront="us",
            language="zh-CN",
            active_subscription=True,
        )
        return self.session

    def logout(self):
        self.session = SessionStatus(connected=False, last_error="已退出登录")
        return self.session

    def get_media_user_token(self):
        return "test-token"


class FakeDownloadResult:
    def __init__(self, downloaded_items=1, skipped_items=0, errors=0):
        self.downloaded_items = downloaded_items
        self.skipped_items = skipped_items
        self.errors = errors

    def to_dict(self):
        return {
            "downloaded_items": self.downloaded_items,
            "skipped_items": self.skipped_items,
            "errors": self.errors,
        }


class WebGuiHelpersTests(unittest.TestCase):
    def test_index_html_includes_wrapper_install_guide(self):
        self.assertIn("Docker 官方安装文档", INDEX_HTML)
        self.assertIn("https://docs.docker.com/desktop/setup/install/mac-install/", INDEX_HTML)
        self.assertIn("docker build -t wrapper-local .", INDEX_HTML)
        self.assertIn("wrapper-latest-10022", INDEX_HTML)
        self.assertIn("AAC</option>", INDEX_HTML)
        self.assertIn("杜比全景声（需要外部 wrapper）", INDEX_HTML)
        self.assertIn('id="select-output-btn"', INDEX_HTML)
        self.assertIn('id="setup-select-output-btn"', INDEX_HTML)
        self.assertIn("background-color: #f3f4f6;", INDEX_HTML)
        self.assertIn("改编自 ${originalProjectName}", INDEX_HTML)

    def test_parse_url_input_splits_lines_and_spaces(self):
        text = "https://music.apple.com/us/album/foo/1\n\n  https://music.apple.com/us/playlist/bar/pl.123  "
        self.assertEqual(
            parse_url_input(text),
            [
                "https://music.apple.com/us/album/foo/1",
                "https://music.apple.com/us/playlist/bar/pl.123",
            ],
        )

    def test_classify_url_detects_playlist(self):
        result = classify_url("https://music.apple.com/us/playlist/test/pl.u-abcdef123")
        self.assertTrue(result["valid"])
        self.assertEqual(result["kind"], "playlist")
        self.assertTrue(result["supported"])

    def test_classify_url_rejects_music_video_for_v1_desktop(self):
        result = classify_url("https://music.apple.com/us/music-video/test/123456789")
        self.assertTrue(result["valid"])
        self.assertEqual(result["kind"], "music-video")
        self.assertFalse(result["supported"])

    def test_build_download_command_is_internal_marker(self):
        command = build_download_command(
            {
                "urls": ["https://music.apple.com/us/album/test/1"],
                "output_path": "/tmp/downloads",
                "overwrite": False,
                "song_codec": "alac",
                "use_wrapper": True,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
            }
        )
        self.assertEqual(command[0], "internal-download")
        self.assertIn("output_path=/tmp/downloads", command)
        self.assertIn("song_codec=alac", command)
        self.assertIn("use_wrapper=True", command)

    def test_gui_settings_defaults_expose_desktop_fields(self):
        self.assertIn("output_path", GUI_SETTINGS_DEFAULTS)
        self.assertIn("log_level", GUI_SETTINGS_DEFAULTS)
        self.assertIn("browser_import_enabled", GUI_SETTINGS_DEFAULTS)
        self.assertIn("setup_completed", GUI_SETTINGS_DEFAULTS)
        self.assertIn("song_codec", GUI_SETTINGS_DEFAULTS)
        self.assertIn("use_wrapper", GUI_SETTINGS_DEFAULTS)
        self.assertIn("wrapper_decrypt_ip", GUI_SETTINGS_DEFAULTS)


class WebGuiApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.paths = AppPaths(base_dir=Path(self.tempdir.name), app_name="GamdlTest")
        self.server = WebGuiServer(
            ("127.0.0.1", 0),
            WebGuiHandler,
            self.paths,
            log_store=None,
            folder_picker=lambda: str(self.paths.default_output_path / "picked"),
        )
        self.server.auth_manager = FakeAuthManager()
        self.server.job_manager = JobManager(
            auth_manager=self.server.auth_manager,
            settings_store=self.server.settings_store,
            paths=self.paths,
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        self.base_url = f"http://{host}:{port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        self.tempdir.cleanup()

    def _get_json(self, path: str) -> dict:
        with urllib.request.urlopen(f"{self.base_url}{path}") as response:
            return json.load(response)

    def _post_json(self, path: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request) as response:
            return json.load(response)

    def test_get_settings_returns_defaults(self):
        data = self._get_json("/api/settings")
        output_path = Path(data["settings"]["output_path"])
        self.assertEqual(output_path.name, "Apple Music Downloader")
        self.assertEqual(output_path.parent.name, "Downloads")
        self.assertTrue(data["settings"]["save_cover"])
        self.assertFalse(data["settings"]["setup_completed"])
        self.assertEqual(data["settings"]["song_codec"], "aac-legacy")
        self.assertFalse(data["settings"]["use_wrapper"])
        self.assertEqual(data["settings"]["wrapper_decrypt_ip"], "127.0.0.1:10022")
        self.assertIn("wrapper_status", data)
        self.assertTrue(data["wrapper_status"]["message"])
        self.assertIn("runtime", data)
        self.assertEqual(data["runtime"]["platform"], platform.system())
        self.assertTrue(data["runtime"]["folder_picker_supported"])

    def test_post_settings_persists_whitelisted_values(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self._post_json(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "log_level": "DEBUG",
                "browser_import_enabled": False,
                "setup_completed": True,
                "song_codec": "aac-legacy",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "unknown_field": "ignored",
            },
        )
        data = self._get_json("/api/settings")
        self.assertEqual(data["settings"]["output_path"], str(remembered_path))
        self.assertEqual(data["settings"]["log_level"], "DEBUG")
        self.assertFalse(data["settings"]["browser_import_enabled"])
        self.assertTrue(data["settings"]["setup_completed"])
        self.assertEqual(data["settings"]["song_codec"], "aac-legacy")
        self.assertFalse(data["settings"]["use_wrapper"])
        self.assertEqual(data["settings"]["wrapper_decrypt_ip"], "127.0.0.1:10022")
        self.assertNotIn("unknown_field", data["settings"])

    def test_validate_settings_output_path_creates_folder(self):
        target = self.paths.app_support_dir / "chosen-downloads"
        data = self._post_json("/api/settings/validate", {"output_path": str(target)})
        self.assertEqual(data["output_path"], str(target))
        self.assertTrue(target.exists())

    def test_select_folder_returns_validated_path(self):
        data = self._post_json("/api/desktop/select-folder", {})
        self.assertTrue(data["selected"])
        self.assertEqual(Path(data["output_path"]).name, "picked")
        self.assertTrue(Path(data["output_path"]).exists())

    def test_about_reports_distribution_audio_policy(self):
        data = self._get_json("/api/about")
        self.assertEqual(data["distribution_audio_default"], "aac-legacy")
        self.assertEqual(data["alac_mode"], "external-wrapper-only")
        self.assertEqual(data["alac_max_spec"], "24-bit / 192 kHz")
        self.assertEqual(
            data["alac_spec_note"],
            "具体取决于歌曲本身是否提供对应规格。",
        )
        self.assertEqual(
            data["original_project_name"],
            "Gamdl (Glomatico's Apple Music Downloader)",
        )
        self.assertEqual(
            data["original_project_url"],
            "https://github.com/glomatico/gamdl",
        )
        self.assertIn("wrapper_status", data)
        self.assertIn("runtime", data)
        self.assertEqual(data["runtime"]["platform"], platform.system())

    def test_auth_import_browser_updates_session(self):
        data = self._post_json("/api/auth/import-browser", {"browser": "chrome"})
        self.assertTrue(data["session"]["connected"])
        self.assertEqual(data["session"]["login_method"], "browser-import")
        self.assertEqual(data["session"]["browser"], "chrome")

    def test_diagnostics_export_creates_zip(self):
        data = self._post_json("/api/diagnostics/export", {})
        bundle_path = Path(data["bundle_path"])
        self.assertTrue(bundle_path.exists())
        self.assertEqual(bundle_path.suffix, ".zip")

    def test_create_job_uses_internal_download_service(self):
        downloads_path = self.paths.app_support_dir / "downloads"
        with patch("gamdl.web_gui.DownloadService.run_sync", return_value=FakeDownloadResult()):
            data = self._post_json(
                "/api/jobs",
                {
                    "url_text": "https://music.apple.com/us/album/test/123456789?i=123456790",
                    "output_path": str(downloads_path),
                    "overwrite": False,
                    "save_cover": True,
                    "log_level": "INFO",
                },
            )
            job_id = data["id"]
            for _ in range(30):
                job = self._get_json(f"/api/jobs/{job_id}")
                if job["status"] != "queued":
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["downloaded_items"], 1)


class DownloadServiceTests(unittest.TestCase):
    def test_create_downloader_uses_app_private_temp_dir(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_api = type(
                "FakeApi",
                (),
                {
                    "active_subscription": True,
                    "storefront": "us",
                    "language": "zh-CN",
                },
            )()

            with (
                patch("gamdl.app.downloads.AppleMusicApi.create", new=AsyncMock(return_value=fake_api)),
                patch("gamdl.app.downloads.ItunesApi"),
                patch("gamdl.app.downloads.AppleMusicInterface"),
                patch("gamdl.app.downloads.AppleMusicSongInterface"),
                patch("gamdl.app.downloads.AppleMusicSongDownloader"),
                patch("gamdl.app.downloads.AppleMusicDownloader"),
                patch("gamdl.app.downloads.AppleMusicBaseDownloader") as base_downloader_cls,
            ):
                asyncio.run(
                    service._create_downloader(
                        DownloadJob(
                            urls=["https://music.apple.com/us/album/test/1?i=2"],
                            output_path="/tmp/downloads",
                        )
                    )
                )

            self.assertEqual(
                base_downloader_cls.call_args.kwargs["temp_path"],
                str(paths.temp_dir),
            )
            self.assertTrue(paths.temp_dir.exists())
        finally:
            tempdir.cleanup()


class AppSettingsStoreTests(unittest.TestCase):
    def test_validate_output_path_rejects_existing_file(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            store = AppSettingsStore(paths)
            bad_path = Path(tempdir.name) / "not-a-directory"
            bad_path.write_text("x", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "不是文件夹"):
                store.validate_output_path(str(bad_path))
        finally:
            tempdir.cleanup()


if __name__ == "__main__":
    unittest.main()
