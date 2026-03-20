import json
import asyncio
import platform
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

from gamdl.app import AppPaths, AppSettingsStore, DownloadJob, DownloadService, SessionStatus
from gamdl.web_gui import (
    INDEX_HTML,
    GUI_SETTINGS_DEFAULTS,
    JobManager,
    Job,
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
    def __init__(
        self,
        downloaded_items=1,
        skipped_items=0,
        errors=0,
        output_path=None,
        latest_media_path=None,
        latest_media_dir=None,
    ):
        self.downloaded_items = downloaded_items
        self.skipped_items = skipped_items
        self.errors = errors
        self.output_path = output_path
        self.latest_media_path = latest_media_path
        self.latest_media_dir = latest_media_dir

    def to_dict(self):
        return {
            "downloaded_items": self.downloaded_items,
            "skipped_items": self.skipped_items,
            "errors": self.errors,
            "output_path": self.output_path,
            "latest_media_path": self.latest_media_path,
            "latest_media_dir": self.latest_media_dir,
        }


class FakeFileActions:
    def __init__(self) -> None:
        self.calls = []
        self.supported = True
        self.chosen_application = "/Applications/IINA.app"

    def open_output(self, path: str) -> None:
        self.calls.append(("open_output", path))

    def open_file(self, path: str, application: str | None = None) -> None:
        self.calls.append(("open_file", path, application))

    def reveal_file(self, path: str) -> None:
        self.calls.append(("reveal_file", path))

    def get_open_with_options(
        self,
        path: str,
        configured_application: str | None = None,
    ):
        options = [
            {
                "label": "Music",
                "kind": "application",
                "is_default": True,
                "application_path": "/Applications/Music.app",
            },
            {"kind": "separator"},
        ]
        if configured_application:
            options.append(
                {
                    "label": f"自定义应用 ({configured_application})",
                    "kind": "application",
                    "is_default": False,
                    "application_path": configured_application,
                }
            )
        options.append(
            {
                "label": "其他…",
                "kind": "pick-application",
                "is_default": False,
                "application_path": None,
            }
        )
        return options

    def choose_application(self) -> str | None:
        return self.chosen_application


@dataclass
class FakeUrlInfo:
    type: str = "song"
    library_type: str | None = None


@dataclass
class FakeDownloadItem:
    final_path: str | None
    media_type: str = "song"
    title: str = "Test Song"
    error: Exception | None = None

    @property
    def media_metadata(self):
        return {"type": self.media_type, "attributes": {"name": self.title}}


class FakeDownloader:
    def __init__(self, queue_items):
        self.interface = type(
            "FakeInterface",
            (),
            {
                "apple_music_api": type("FakeApi", (), {"storefront": "us"})(),
            },
        )()
        self.queue_items = queue_items

    def get_url_info(self, _url):
        return FakeUrlInfo()

    async def get_download_queue(self, _url_info):
        return list(self.queue_items)

    async def download(self, _download_item):
        return None


class WebGuiHelpersTests(unittest.TestCase):
    def test_index_html_includes_wrapper_install_guide(self):
        self.assertIn("Docker 官方安装文档", INDEX_HTML)
        self.assertIn("https://docs.docker.com/desktop/setup/install/mac-install/", INDEX_HTML)
        self.assertIn("docker build -t wrapper-local .", INDEX_HTML)
        self.assertIn("wrapper-latest-10022", INDEX_HTML)
        self.assertIn("免费 开源 纯净", INDEX_HTML)
        self.assertIn("brand-head", INDEX_HTML)
        self.assertIn("brand-mark", INDEX_HTML)
        self.assertIn('linearGradient id="brand-bg"', INDEX_HTML)
        self.assertIn("AAC</option>", INDEX_HTML)
        self.assertIn("杜比全景声（需要外部 wrapper）", INDEX_HTML)
        self.assertIn('id="select-output-btn"', INDEX_HTML)
        self.assertIn('id="setup-select-output-btn"', INDEX_HTML)
        self.assertIn("background-color: #f3f4f6;", INDEX_HTML)
        self.assertIn("改编自 ${originalProjectName}", INDEX_HTML)
        self.assertIn("修改者", INDEX_HTML)
        self.assertIn("请确保从 GitHub 官方项目页下载", INDEX_HTML)
        self.assertIn("https://github.com/Mrgu2/gamdl/tree/codex/fix-wrapper-alac-download", INDEX_HTML)
        self.assertIn("打开下载目录", INDEX_HTML)
        self.assertIn("显示文件位置", INDEX_HTML)
        self.assertIn('id="open-file-application"', INDEX_HTML)
        self.assertIn("finder-menu", INDEX_HTML)
        self.assertIn("split-pill-anchor", INDEX_HTML)
        self.assertIn("split-pill-toggle", INDEX_HTML)
        self.assertIn("event.target.closest('.split-pill-anchor')", INDEX_HTML)
        self.assertIn("event.key === 'Escape'", INDEX_HTML)
        self.assertIn("overflow-y: auto;", INDEX_HTML)
        self.assertIn('data-page="convert"', INDEX_HTML)
        self.assertIn('id="page-convert"', INDEX_HTML)
        self.assertIn('id="submit-convert-btn"', INDEX_HTML)
        self.assertIn('id="select-convert-file-btn"', INDEX_HTML)
        self.assertIn('id="select-convert-input-folder-btn"', INDEX_HTML)
        self.assertIn('id="select-convert-output-btn"', INDEX_HTML)
        self.assertIn("ffmpeg 转换，默认保持原采样率", INDEX_HTML)
        self.assertIn("if (shouldPreserveOpenMenu && state.openWithMenuJobId)", INDEX_HTML)
        self.assertIn("refreshJobs({ preserveOpenMenu: true })", INDEX_HTML)
        self.assertIn("position: fixed;", INDEX_HTML)
        self.assertIn("function positionOpenWithMenu(jobId)", INDEX_HTML)
        self.assertIn('.split-pill-anchor[data-job-id="${jobId}"] .split-pill-toggle', INDEX_HTML)
        self.assertIn("const desiredMenuWidth = 300;", INDEX_HTML)
        self.assertIn("const fallbackMinWidth = 150;", INDEX_HTML)
        self.assertIn("const seamOverlap = 1;", INDEX_HTML)
        self.assertIn("const gap = 0;", INDEX_HTML)
        self.assertIn("openWithMenuRequestId: 0", INDEX_HTML)
        self.assertIn("openWithOptionsCache: {}", INDEX_HTML)
        self.assertIn("function getCachedOpenWithOptions(jobId)", INDEX_HTML)
        self.assertIn("function cacheOpenWithOptions(jobId, latestMediaPath, data)", INDEX_HTML)
        self.assertIn("anchor.classList.toggle('loading', loading)", INDEX_HTML)
        self.assertIn("const requestId = ++state.openWithMenuRequestId;", INDEX_HTML)
        self.assertIn("menu.hidden = true;", INDEX_HTML)
        self.assertIn("menu.hidden = false;", INDEX_HTML)
        self.assertIn("function revealMeasuredOpenWithMenu(jobId)", INDEX_HTML)
        self.assertIn("menu.classList.add('measuring');", INDEX_HTML)
        self.assertIn("menu.classList.remove('measuring');", INDEX_HTML)

    def test_shell_panels_do_not_blur_fixed_menu_ancestors(self):
        match = re.search(r"\.sidebar, \.main-panel \{(?P<block>.*?)\n    \}", INDEX_HTML, re.S)
        self.assertIsNotNone(match)
        self.assertNotIn("backdrop-filter", match.group("block"))
        self.assertIn("position: fixed;", INDEX_HTML)

    def test_split_pill_anchor_avoids_geometry_changing_motion(self):
        anchor_match = re.search(r"\.split-pill-anchor \{(?P<block>.*?)\n    \}", INDEX_HTML, re.S)
        self.assertIsNotNone(anchor_match)
        anchor_block = anchor_match.group("block")
        self.assertIn(
            "transition: background-color .18s ease, border-color .18s ease, box-shadow .18s ease;",
            anchor_block,
        )
        self.assertNotIn("transition: .18s ease;", anchor_block)

        hover_match = re.search(r"\.split-pill-anchor:hover \{(?P<block>.*?)\n    \}", INDEX_HTML, re.S)
        self.assertIsNotNone(hover_match)
        self.assertNotIn("transform", hover_match.group("block"))

    def test_finder_menu_measures_while_hidden(self):
        menu_match = re.search(r"\.finder-menu \{(?P<block>.*?)\n    \}", INDEX_HTML, re.S)
        self.assertIsNotNone(menu_match)
        self.assertNotIn("backdrop-filter", menu_match.group("block"))
        self.assertIn(".finder-menu.measuring {", INDEX_HTML)

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
        self.assertIn("open_file_application", GUI_SETTINGS_DEFAULTS)


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
            file_picker=lambda: str(self.paths.default_output_path / "picked" / "input.m4a"),
            input_folder_picker=lambda: str(self.paths.default_output_path / "picked-input"),
        )
        self.server.auth_manager = FakeAuthManager()
        self.server.file_actions = FakeFileActions()
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

    def _post_json_error(self, path: str, payload: dict) -> tuple[int, dict]:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(request)
        return error.exception.code, json.loads(error.exception.read().decode("utf-8"))

    def _store_job(self, job: Job) -> None:
        with self.server.job_manager.jobs_lock:
            self.server.job_manager.jobs[job.id] = job

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
        self.assertTrue(data["runtime"]["file_picker_supported"])
        self.assertTrue(data["runtime"]["conversion_supported"])
        self.assertEqual(
            data["runtime"]["file_actions_supported"],
            self.server.file_actions.supported,
        )

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
                "open_file_application": "VLC",
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
        self.assertEqual(data["settings"]["open_file_application"], "VLC")
        self.assertNotIn("unknown_field", data["settings"])

    def test_validate_settings_output_path_creates_folder(self):
        target = self.paths.app_support_dir / "chosen-downloads"
        data = self._post_json("/api/settings/validate", {"output_path": str(target)})
        self.assertEqual(data["output_path"], str(target))
        self.assertTrue(target.exists())

    def test_post_settings_allows_clearing_open_file_application(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self._post_json(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "open_file_application": "VLC",
            },
        )
        self._post_json(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "open_file_application": "",
            },
        )
        data = self._get_json("/api/settings")
        self.assertEqual(data["settings"]["open_file_application"], "")

    def test_select_folder_returns_validated_path(self):
        data = self._post_json("/api/desktop/select-folder", {})
        self.assertTrue(data["selected"])
        self.assertEqual(Path(data["output_path"]).name, "picked")
        self.assertTrue(Path(data["output_path"]).exists())

    def test_select_file_returns_existing_input_path(self):
        selected = Path(self.server.file_picker())
        selected.parent.mkdir(parents=True, exist_ok=True)
        selected.write_text("audio", encoding="utf-8")

        data = self._post_json("/api/desktop/select-file", {})

        self.assertTrue(data["selected"])
        self.assertEqual(data["input_path"], str(selected.resolve()))

    def test_select_input_folder_returns_existing_directory(self):
        selected = Path(self.server.input_folder_picker())
        selected.mkdir(parents=True, exist_ok=True)

        data = self._post_json("/api/desktop/select-input-folder", {})

        self.assertTrue(data["selected"])
        self.assertEqual(data["input_path"], str(selected.resolve()))

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
        self.assertEqual(data["modified_by"], "@Mrgu2")
        self.assertEqual(
            data["modified_project_url"],
            "https://github.com/Mrgu2/gamdl/tree/codex/fix-wrapper-alac-download",
        )
        self.assertIn("请确保从 GitHub @Mrgu2 下载该软件", data["download_safety_note"])
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
        latest_media_path = str(downloads_path / "Artist" / "Song.m4a")
        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(
                output_path=str(downloads_path),
                latest_media_path=latest_media_path,
                latest_media_dir=str(Path(latest_media_path).parent),
            ),
        ):
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
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["downloaded_items"], 1)
            self.assertEqual(job["result"]["latest_media_path"], latest_media_path)
            self.assertEqual(job["result"]["latest_media_dir"], str(Path(latest_media_path).parent))

    def test_create_convert_job_uses_internal_conversion_service(self):
        input_file = self.paths.app_support_dir / "input" / "Track.m4a"
        input_file.parent.mkdir(parents=True, exist_ok=True)
        input_file.write_text("audio", encoding="utf-8")
        output_path = self.paths.app_support_dir / "converted"

        with patch(
            "gamdl.web_gui.ConversionService.run",
            return_value=type(
                "FakeConversionResult",
                (),
                {
                    "errors": 0,
                    "to_dict": lambda self: {
                        "total_files": 1,
                        "converted_files": 1,
                        "skipped_files": 0,
                        "errors": 0,
                        "latest_media_path": str(output_path / "Track.flac"),
                        "latest_media_dir": str(output_path),
                    },
                },
            )(),
        ):
            data = self._post_json(
                "/api/jobs",
                {
                    "kind": "convert",
                    "input_mode": "file",
                    "input_path": str(input_file),
                    "output_path": str(output_path),
                    "target_format": "flac",
                    "overwrite": False,
                },
            )
            job_id = data["id"]
            for _ in range(30):
                job = self._get_json(f"/api/jobs/{job_id}")
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.05)
            self.assertEqual(job["kind"], "convert")
            self.assertEqual(job["status"], "completed")
            self.assertEqual(job["result"]["converted_files"], 1)

    def test_open_output_action_uses_job_output_path(self):
        output_path = self.paths.default_output_path / "album"
        output_path.mkdir(parents=True, exist_ok=True)
        job = Job(
            id="job-open-output",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(output_path)},
            command=[],
            status="completed",
        )
        self._store_job(job)

        data = self._post_json("/api/jobs/job-open-output/open-output", {})

        self.assertTrue(data["ok"])
        self.assertEqual(self.server.file_actions.calls[-1], ("open_output", str(output_path)))

    def test_open_latest_file_action_uses_latest_media_path(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        job = Job(
            id="job-open-file",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        data = self._post_json("/api/jobs/job-open-file/open-latest-file", {})

        self.assertTrue(data["ok"])
        self.assertEqual(self.server.file_actions.calls[-1], ("open_file", str(latest_media_path), None))

    def test_open_latest_file_action_accepts_configured_application(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        job = Job(
            id="job-open-file-app",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        data = self._post_json(
            "/api/jobs/job-open-file-app/open-latest-file",
            {"application_path": "VLC"},
        )

        self.assertTrue(data["ok"])
        self.assertEqual(
            self.server.file_actions.calls[-1],
            ("open_file", str(latest_media_path), "VLC"),
        )

    def test_open_latest_file_action_can_choose_application(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        job = Job(
            id="job-open-file-picker",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        data = self._post_json(
            "/api/jobs/job-open-file-picker/open-latest-file",
            {"choose_application": True},
        )

        self.assertTrue(data["ok"])
        self.assertEqual(
            self.server.file_actions.calls[-1],
            ("open_file", str(latest_media_path), self.server.file_actions.chosen_application),
        )

    def test_get_open_with_options_returns_default_and_other(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        self.server.settings_store.save(
            {
                "output_path": str(self.paths.default_output_path),
                "open_file_application": "VLC",
            }
        )
        job = Job(
            id="job-open-with-options",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        data = self._get_json("/api/jobs/job-open-with-options/open-with-options")

        self.assertTrue(data["supported"])
        self.assertEqual(data["latest_media_path"], str(latest_media_path))
        self.assertEqual(data["options"][0]["label"], "Music")
        self.assertEqual(data["options"][-1]["kind"], "pick-application")

    def test_get_open_with_options_returns_400_without_latest_file(self):
        job = Job(
            id="job-open-with-missing",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(self.paths.default_output_path)},
            command=[],
            status="completed",
            result={"latest_media_path": None},
        )
        self._store_job(job)

        with self.assertRaises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(f"{self.base_url}/api/jobs/job-open-with-missing/open-with-options")

        self.assertEqual(error.exception.code, 400)

    def test_reveal_latest_file_action_uses_latest_media_path(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        job = Job(
            id="job-reveal-file",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        data = self._post_json("/api/jobs/job-reveal-file/reveal-latest-file", {})

        self.assertTrue(data["ok"])
        self.assertEqual(self.server.file_actions.calls[-1], ("reveal_file", str(latest_media_path)))

    def test_open_latest_file_returns_400_when_job_has_no_media_file(self):
        output_path = self.paths.default_output_path / "album"
        output_path.mkdir(parents=True, exist_ok=True)
        job = Job(
            id="job-no-file",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(output_path)},
            command=[],
            status="completed",
            result={"latest_media_path": None},
        )
        self._store_job(job)

        status, data = self._post_json_error("/api/jobs/job-no-file/open-latest-file", {})

        self.assertEqual(status, 400)
        self.assertIn("没有可打开的下载文件", data["error"])

    def test_open_output_returns_404_for_unknown_job(self):
        status, data = self._post_json_error("/api/jobs/missing-job/open-output", {})

        self.assertEqual(status, 404)
        self.assertEqual(data["error"], "Job not found")


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

    def test_run_tracks_last_successful_media_file(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            first_path = Path(tempdir.name) / "one" / "Track 1.m4a"
            second_path = Path(tempdir.name) / "two" / "Track 2.m4a"
            first_path.parent.mkdir(parents=True, exist_ok=True)
            second_path.parent.mkdir(parents=True, exist_ok=True)
            first_path.write_text("1", encoding="utf-8")
            second_path.write_text("2", encoding="utf-8")
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(final_path=str(first_path), title="Track 1"),
                    FakeDownloadItem(final_path=str(second_path), title="Track 2"),
                ]
            )

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/album/test/1?i=2"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.downloaded_items, 2)
            self.assertEqual(result.output_path, str(Path(tempdir.name) / "downloads"))
            self.assertEqual(result.latest_media_path, str(second_path.resolve()))
            self.assertEqual(result.latest_media_dir, str(second_path.resolve().parent))
        finally:
            tempdir.cleanup()

    def test_run_leaves_latest_media_empty_when_all_items_fail_or_skip(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            missing_path = Path(tempdir.name) / "missing" / "Track 1.m4a"
            fake_downloader = FakeDownloader([FakeDownloadItem(final_path=str(missing_path), error=Exception("boom"))])

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/album/test/1?i=2"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.downloaded_items, 0)
            self.assertEqual(result.errors, 1)
            self.assertIsNone(result.latest_media_path)
            self.assertIsNone(result.latest_media_dir)
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
