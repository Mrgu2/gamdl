import json
import asyncio
import http.client
import os
import platform
import queue
import re
import socket
import tempfile
import threading
import time
import unittest
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from gamdl.api.exceptions import ApiError
from gamdl.app import AppPaths, AppSettingsStore, DownloadJob, DownloadService, JobCancelledError, SessionStatus, resolve_executable
from gamdl.app.auth import TokenDeletionError
from gamdl.app.conversion import ConversionFormat, ConversionResult, ConversionService
from gamdl.app.downloads import DownloadResult
from gamdl.downloader import GamdlError
from gamdl.interface.types import PlaylistTags
from gamdl.web_gui import (
    INDEX_HTML,
    GUI_SETTINGS_DEFAULTS,
    INVALID_OPEN_APPLICATION_ERROR,
    JobManager,
    Job,
    MAX_JSON_BODY_BYTES,
    WebGuiHandler,
    WebGuiServer,
    WebGuiServerIPv6,
    _format_host_for_url,
    build_download_command,
    classify_url,
    create_server,
    main as web_gui_main,
    parse_url_input,
    validate_bind_host,
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

    def login_with_webview(self, browser=None, language: str = "zh-CN"):
        self.session = SessionStatus(
            connected=True,
            login_method="browser-import",
            browser=getattr(browser, "value", None),
            storefront="us",
            language=language,
            active_subscription=True,
        )
        return self.session

    def logout(self):
        self.session = SessionStatus(connected=False, last_error="已退出登录")
        return self.session

    def invalidate_session(self, reason: str = "Apple Music 登录已失效，请重新登录。"):
        self.session = SessionStatus(connected=False, last_error=reason)
        return self.session

    def get_media_user_token(self):
        return "test-token"


class WebGuiSecurityTests(unittest.TestCase):
    def test_validate_bind_host_accepts_loopback_addresses(self):
        self.assertEqual(validate_bind_host("127.0.0.1"), "127.0.0.1")
        self.assertEqual(validate_bind_host("::1"), "::1")
        self.assertEqual(validate_bind_host("localhost"), "localhost")

    def test_format_host_for_url_wraps_ipv6_loopback(self):
        self.assertEqual(_format_host_for_url("::1"), "[::1]")
        self.assertEqual(_format_host_for_url("127.0.0.1"), "127.0.0.1")

    def test_ipv6_server_uses_ipv6_address_family(self):
        self.assertEqual(WebGuiServerIPv6.address_family, socket.AF_INET6)

    def test_validate_bind_host_rejects_non_loopback_addresses(self):
        with self.assertRaisesRegex(ValueError, "仅允许绑定到本机回环地址"):
            validate_bind_host("0.0.0.0")
        with self.assertRaisesRegex(ValueError, "仅允许绑定到本机回环地址"):
            validate_bind_host("192.168.1.10")

    def test_create_server_rejects_non_loopback_host(self):
        with self.assertRaisesRegex(ValueError, "仅允许绑定到本机回环地址"):
            create_server("0.0.0.0", 8765)

    def test_index_html_escapes_preview_values_before_innerhtml_render(self):
        self.assertIn("<strong>${escapeHtml(item.label)}</strong>", INDEX_HTML)
        self.assertIn("<span class=\"muted\">${escapeHtml(item.url)}</span>", INDEX_HTML)

    def test_index_html_escapes_session_values_before_innerhtml_render(self):
        self.assertIn("<div class=\"list-head\"><strong>${escapeHtml(label)}</strong><span class=\"muted\">${escapeHtml(value)}</span></div>", INDEX_HTML)
        self.assertIn("if (cancelRequested && status === 'running')", INDEX_HTML)
        self.assertIn("job.cancel_requested ? '取消中，当前文件完成后停止' : '取消'", INDEX_HTML)
        self.assertIn("return ['取消中，当前文件完成后停止', 'badge warn'];", INDEX_HTML)
        self.assertIn("function currentMetadataLanguage()", INDEX_HTML)

    def test_index_html_preserves_custom_metadata_language_input_when_toggling_presets(self):
        start = INDEX_HTML.index("function syncMetadataLanguageControls()")
        end = INDEX_HTML.index("function setMetadataLanguageValue(", start)
        sync_fn = INDEX_HTML[start:end]
        self.assertNotIn("customInput.value = '';", sync_fn)

    def test_index_html_forces_hidden_attribute_to_hide_elements(self):
        self.assertIn("[hidden] { display: none !important; }", INDEX_HTML)


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
    id: str | None = "123"
    sub_id: str | None = None


@dataclass
class FakeDownloadItem:
    final_path: str | None
    media_type: str = "song"
    title: str = "Test Song"
    url: str | None = None
    source_context: str | None = None
    artist_folder_name: str | None = None
    playlist_metadata: dict | None = None
    playlist_tags: PlaylistTags | None = None
    sidecar_failures: list[dict[str, str | None]] | None = None
    error: Exception | None = None

    @property
    def media_metadata(self):
        attributes = {"name": self.title}
        if self.url:
            attributes["url"] = self.url
        return {"type": self.media_type, "attributes": attributes}


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
        self.assertIn('rel="icon"', INDEX_HTML)
        self.assertIn("https://docs.docker.com/desktop/setup/install/mac-install/", INDEX_HTML)
        self.assertIn("https://github.com/WorldObservationLog/wrapper/releases", INDEX_HTML)
        self.assertIn("Wrapper Releases", INDEX_HTML)
        self.assertIn("curl -fsSL https://api.github.com/repos/WorldObservationLog/wrapper/releases/latest", INDEX_HTML)
        self.assertIn("docker build --platform linux/amd64 -t wrapper-local .", INDEX_HTML)
        self.assertIn("--platform linux/amd64", INDEX_HTML)
        self.assertIn("X-Gamdl-Request-Token", INDEX_HTML)
        self.assertIn('echo "wrapper ok"', INDEX_HTML)
        self.assertIn('echo "dockerfile ok"', INDEX_HTML)
        self.assertIn("ls -l wrapper.zip", INDEX_HTML)
        self.assertIn("COPY ./wrapper /app: not found", INDEX_HTML)
        self.assertIn("127.0.0.1:10022 connection refused", INDEX_HTML)
        self.assertIn("wrapper-latest-10022", INDEX_HTML)
        self.assertNotIn("-p 30022:30022", INDEX_HTML)
        self.assertIn("-p 127.0.0.1:10022:10022", INDEX_HTML)
        self.assertIn("-p 127.0.0.1:20022:20022", INDEX_HTML)
        self.assertIn('-e args="-H 0.0.0.0 -D 10022 -M 20022"', INDEX_HTML)
        self.assertNotIn("免费 开源 纯净", INDEX_HTML)
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
        self.assertIn("请确保从 GitHub @Mrgu2 下载该软件", INDEX_HTML)
        self.assertIn("https://github.com/Mrgu2/gu_music_downloader", INDEX_HTML)
        self.assertIn("打开下载目录", INDEX_HTML)
        self.assertIn("显示文件位置", INDEX_HTML)
        self.assertIn('id="open-file-application"', INDEX_HTML)
        self.assertIn('id="network-mode"', INDEX_HTML)
        self.assertIn('id="proxy-url"', INDEX_HTML)
        self.assertIn("自动（推荐）", INDEX_HTML)
        self.assertIn("如果你电脑开了代理工具，下载偶尔失败时，可以切到“直连”试试。", INDEX_HTML)
        self.assertIn("syncNetworkModeControls", INDEX_HTML)
        self.assertIn('id="save-playlist"', INDEX_HTML)
        self.assertIn("payload.save_playlist = document.getElementById('save-playlist').value === 'true';", INDEX_HTML)
        self.assertIn("finder-menu", INDEX_HTML)
        self.assertIn("split-pill-anchor", INDEX_HTML)
        self.assertIn("split-pill-toggle", INDEX_HTML)
        self.assertIn("event.target.closest('.split-pill-anchor')", INDEX_HTML)
        self.assertIn("event.key === 'Escape'", INDEX_HTML)
        self.assertIn("overflow-y: auto;", INDEX_HTML)
        self.assertIn('data-page="convert"', INDEX_HTML)
        self.assertIn('id="page-convert"', INDEX_HTML)
        self.assertIn('id="submit-convert-btn"', INDEX_HTML)
        self.assertIn('id="open-logs-folder-btn"', INDEX_HTML)
        self.assertIn('id="logs-folder-path"', INDEX_HTML)
        self.assertIn('for="setup-browser-select"', INDEX_HTML)
        self.assertIn('for="browser-select"', INDEX_HTML)
        self.assertIn('aria-label="首次设置登录浏览器"', INDEX_HTML)
        self.assertIn('aria-label="账号页登录浏览器"', INDEX_HTML)
        self.assertIn("sr-only", INDEX_HTML)
        self.assertIn("log-scroll-panel", INDEX_HTML)
        self.assertIn("job-log-scroll", INDEX_HTML)
        self.assertIn("app-log-scroll", INDEX_HTML)
        self.assertIn("selectionInsideLogs", INDEX_HTML)
        self.assertIn("shouldStickToBottom", INDEX_HTML)
        self.assertIn("output.contains(activeSelection.anchorNode)", INDEX_HTML)
        self.assertIn("scroller.scrollTop = scroller.scrollHeight", INDEX_HTML)
        self.assertIn("throw new Error('请先完成 Apple Music 登录。');", INDEX_HTML)
        self.assertIn("throw new Error('当前已关闭浏览器导入功能。请先在设置中重新开启。');", INDEX_HTML)
        self.assertIn("throw new Error('当前所选音质需要启用外部 wrapper。请先打开“启用 wrapper”，或切回 AAC。');", INDEX_HTML)
        self.assertIn("button.disabled = !browserImportEnabled;", INDEX_HTML)
        self.assertIn("button.title = browserImportMessage;", INDEX_HTML)
        self.assertIn("const canShowOutputAction = supportsFileActions && Boolean(job.payload?.output_path);", INDEX_HTML)
        self.assertIn("const errorCount = typeof result.errors === 'number' ? result.errors : (job.error_message ? 1 : 0);", INDEX_HTML)
        self.assertIn('id="select-convert-file-btn"', INDEX_HTML)
        self.assertIn('id="select-convert-input-folder-btn"', INDEX_HTML)
        self.assertIn('id="select-convert-output-btn"', INDEX_HTML)
        self.assertIn('id="theme-select"', INDEX_HTML)
        self.assertIn('id="start-wrapper-btn"', INDEX_HTML)
        self.assertIn('id="wrapper-action-copy"', INDEX_HTML)
        self.assertIn("function startWrapper()", INDEX_HTML)
        self.assertIn("/api/wrapper/start", INDEX_HTML)
        self.assertIn("使用 ffmpeg 转换本地文件。", INDEX_HTML)
        self.assertIn("if (shouldPreserveOpenMenu && state.openWithMenuJobId)", INDEX_HTML)
        self.assertIn("refreshJobs({ preserveOpenMenu: true })", INDEX_HTML)
        self.assertIn("position: fixed;", INDEX_HTML)
        self.assertIn("function positionOpenWithMenu(jobId)", INDEX_HTML)
        self.assertIn('id="open-with-menu"', INDEX_HTML)
        self.assertIn('id="artist-auto-select"', INDEX_HTML)
        self.assertIn("请选择艺术家内容", INDEX_HTML)
        self.assertIn("function syncArtistDownloadOptionsFromInput()", INDEX_HTML)
        self.assertIn("url.includes('/artist/')", INDEX_HTML)
        self.assertIn("if (hasArtist) {", INDEX_HTML)
        self.assertIn("syncArtistDownloadOptions([]);", INDEX_HTML)
        self.assertIn("document.getElementById('url-input').addEventListener('input', syncArtistDownloadOptionsFromInput);", INDEX_HTML)
        self.assertIn("本次任务里的所有 Artist 链接会共用这一下载内容", INDEX_HTML)
        self.assertIn("function retryFailedItems(jobId)", INDEX_HTML)
        self.assertIn("/retry-failed", INDEX_HTML)
        self.assertIn("function getOpenWithMenu()", INDEX_HTML)
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
        self.assertIn("<strong>${escapeHtml(item.label)}</strong>", INDEX_HTML)
        self.assertIn('<span class="muted">${escapeHtml(item.url)}</span>', INDEX_HTML)
        self.assertIn("<strong>${escapeHtml(label)}</strong><span class=\"muted\">${escapeHtml(value)}</span>", INDEX_HTML)
        self.assertNotIn('id="open-with-menu-${job.id}"', INDEX_HTML)
        self.assertIn('data-theme="warm"', INDEX_HTML)
        self.assertIn('body[data-theme="cool"]', INDEX_HTML)
        self.assertIn("function applyTheme(theme)", INDEX_HTML)

    def test_shell_panels_do_not_blur_fixed_menu_ancestors(self):
        match = re.search(r"\.sidebar, \.main-panel \{(?P<block>.*?)\n    \}", INDEX_HTML, re.S)
        self.assertIsNotNone(match)
        self.assertNotIn("backdrop-filter", match.group("block"))
        self.assertIn("position: fixed;", INDEX_HTML)

    def test_cool_theme_nav_hover_preserves_active_state(self):
        self.assertIn(".nav-btn:hover:not(.active)", INDEX_HTML)
        match = re.search(
            r'body\[data-theme="cool"\] \.nav-btn:hover:not\(\.active\) \{(?P<block>.*?)\n    \}',
            INDEX_HTML,
            re.S,
        )
        self.assertIsNotNone(match)
        block = match.group("block")
        self.assertIn("background: rgba(10, 132, 255, .06);", block)
        self.assertIn("border-color: rgba(148, 163, 184, .22);", block)

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

    def test_classify_url_accepts_artist_for_desktop(self):
        result = classify_url("https://music.apple.com/us/artist/test/123456789")
        self.assertTrue(result["valid"])
        self.assertEqual(result["kind"], "artist")
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
                "save_playlist": True,
                "language": "ja-JP",
                "song_codec": "alac",
                "artist_auto_select": "top-songs",
                "use_wrapper": True,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
            }
        )
        self.assertEqual(command[0], "internal-download")
        self.assertIn("output_path=/tmp/downloads", command)
        self.assertIn("save_playlist=True", command)
        self.assertIn("language=ja-JP", command)
        self.assertIn("song_codec=alac", command)
        self.assertIn("artist_auto_select=top-songs", command)
        self.assertIn("use_wrapper=True", command)

    def test_gui_settings_defaults_expose_desktop_fields(self):
        self.assertIn("output_path", GUI_SETTINGS_DEFAULTS)
        self.assertIn("log_level", GUI_SETTINGS_DEFAULTS)
        self.assertIn("browser_import_enabled", GUI_SETTINGS_DEFAULTS)
        self.assertIn("setup_completed", GUI_SETTINGS_DEFAULTS)
        self.assertIn("song_codec", GUI_SETTINGS_DEFAULTS)
        self.assertIn("language", GUI_SETTINGS_DEFAULTS)
        self.assertIn("theme", GUI_SETTINGS_DEFAULTS)
        self.assertIn("use_wrapper", GUI_SETTINGS_DEFAULTS)
        self.assertIn("wrapper_decrypt_ip", GUI_SETTINGS_DEFAULTS)
        self.assertIn("artist_auto_select", GUI_SETTINGS_DEFAULTS)
        self.assertIn("open_file_application", GUI_SETTINGS_DEFAULTS)


class WebGuiApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self._proxy_env_backup = {
            name: os.environ.get(name)
            for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
        }
        self._getproxies_backup = urllib.request.getproxies
        self._getproxies_env_backup = urllib.request.getproxies_environment
        for name in self._proxy_env_backup:
            os.environ.pop(name, None)
        urllib.request.getproxies = lambda: {}
        urllib.request.getproxies_environment = lambda: {}
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
        self.server.job_manager.shutdown()
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
        self.host = host
        self.port = port
        self.base_url = f"http://{host}:{port}"
        self.app_base_url = f"{self.base_url}{self.server.session_base_path}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1)
        for name, value in self._proxy_env_backup.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        urllib.request.getproxies = self._getproxies_backup
        urllib.request.getproxies_environment = self._getproxies_env_backup
        self.tempdir.cleanup()

    def _request(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        headers: dict[str, str] | None = None,
        include_token: bool = True,
        use_session_path: bool = True,
    ) -> tuple[int, bytes, dict[str, str]]:
        body = b""
        request_headers = {"Connection": "close"}
        if include_token:
            request_headers["X-Gamdl-Request-Token"] = self.server.api_request_token
        if headers:
            request_headers.update(headers)
        if payload is not None:
            body = json.dumps(payload).encode("utf-8")
            request_headers.setdefault("Content-Type", "application/json")
        target = f"{self.server.session_base_path}{path}" if use_session_path else path
        connection = http.client.HTTPConnection(self.host, self.port, timeout=5)
        try:
            connection.request(method, target, body=body, headers=request_headers)
            response = connection.getresponse()
            try:
                payload_bytes = response.read()
                response_headers = {key: value for key, value in response.getheaders()}
                return response.status, payload_bytes, response_headers
            finally:
                response.close()
        finally:
            connection.close()

    def _get_json(self, path: str) -> dict:
        status, body, _headers = self._request(path)
        self.assertEqual(status, 200)
        return json.loads(body.decode("utf-8"))

    def _post_json(self, path: str, payload: dict) -> dict:
        status, body, _headers = self._request(path, method="POST", payload=payload)
        self.assertIn(status, {200, 201})
        return json.loads(body.decode("utf-8"))

    def _post_json_error(self, path: str, payload: dict) -> tuple[int, dict]:
        status, body, _headers = self._request(path, method="POST", payload=payload)
        return status, json.loads(body.decode("utf-8"))

    def _wait_for_terminal_job(self, job_id: str, timeout: float = 1.5) -> dict:
        deadline = time.time() + timeout
        job = self._get_json(f"/api/jobs/{job_id}")
        while job["status"] not in {"completed", "failed", "cancelled"}:
            if time.time() >= deadline:
                self.fail(f"job {job_id} did not reach terminal state within {timeout} seconds")
            time.sleep(0.05)
            job = self._get_json(f"/api/jobs/{job_id}")
        return job

    def test_main_prints_session_url_for_manual_copy(self):
        fake_server = SimpleNamespace(
            local_url=lambda host: "http://127.0.0.1:8765/session-token/",
            public_origin=lambda host: "http://127.0.0.1:8765",
            serve_forever=lambda: None,
            server_close=lambda: None,
        )

        with (
            patch("gamdl.web_gui.argparse.ArgumentParser.parse_args", return_value=SimpleNamespace(host="127.0.0.1", port=8765, no_open=True)),
            patch("gamdl.web_gui.AppSettingsStore") as settings_store_cls,
            patch("gamdl.web_gui.configure_app_logging"),
            patch("gamdl.web_gui.create_server", return_value=fake_server),
            patch("builtins.print") as print_mock,
        ):
            settings_store_cls.return_value.load.return_value = SimpleNamespace(log_level="INFO")
            web_gui_main()

        printed_lines = [" ".join(str(part) for part in call.args) for call in print_mock.call_args_list]
        self.assertTrue(any("http://127.0.0.1:8765/session-token/" in line for line in printed_lines))

    def test_post_rejects_missing_local_request_token(self):
        status, body, _headers = self._request(
            "/api/auth/logout",
            method="POST",
            payload={},
            headers={"Content-Type": "application/json"},
            include_token=False,
        )
        self.assertEqual(status, 403)
        self.assertIn("本地会话令牌", body.decode("utf-8"))

    def test_post_rejects_oversized_json_body(self):
        target = f"{self.server.session_base_path}/api/preview"
        request = (
            f"POST {target} HTTP/1.1\r\n"
            f"Host: {self.host}:{self.port}\r\n"
            "Connection: close\r\n"
            "Content-Type: application/json\r\n"
            f"X-Gamdl-Request-Token: {self.server.api_request_token}\r\n"
            f"Content-Length: {MAX_JSON_BODY_BYTES + 1}\r\n"
            "\r\n"
        )
        with socket.create_connection((self.host, self.port), timeout=5) as client:
            client.sendall(request.encode("ascii"))
            chunks = []
            while True:
                chunk = client.recv(4096)
                if not chunk:
                    break
                chunks.append(chunk)
            response = b"".join(chunks)

        self.assertIn(b" 413 ", response)
        self.assertIn("请求体过大".encode("utf-8"), response)

    def test_get_rejects_non_loopback_host_header(self):
        status, body, _headers = self._request(
            "/",
            headers={"Host": "evil.example"},
        )
        self.assertEqual(status, 403)
        self.assertIn("本机回环地址", body.decode("utf-8"))

    def test_post_rejects_non_json_content_type(self):
        status, body, _headers = self._request(
            "/api/settings",
            method="POST",
            payload={},
            headers={
                "Content-Type": "text/plain",
            },
        )
        self.assertEqual(status, 415)
        self.assertIn("application/json", body.decode("utf-8"))

    def test_get_index_injects_runtime_request_token(self):
        status, body, headers = self._request("/", include_token=False)
        self.assertEqual(status, 200)
        html = body.decode("utf-8")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])

        self.assertIn(self.server.api_request_token, html)
        self.assertNotIn("__GAMDL_API_REQUEST_TOKEN__", html)

    def test_plain_root_no_longer_serves_session_page(self):
        status, _body, _headers = self._request("/", include_token=False, use_session_path=False)
        self.assertEqual(status, 404)

    def _store_job(self, job: Job) -> None:
        with self.server.job_manager.jobs_lock:
            self.server.job_manager.jobs[job.id] = job

    def test_get_settings_returns_defaults(self):
        status, body, headers = self._request("/api/settings")
        self.assertEqual(status, 200)
        data = json.loads(body.decode("utf-8"))
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["Referrer-Policy"], "no-referrer")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        output_path = Path(data["settings"]["output_path"])
        self.assertEqual(output_path.name, "Apple Music Downloader")
        self.assertEqual(output_path.parent.name, "Downloads")
        self.assertTrue(data["settings"]["save_cover"])
        self.assertFalse(data["settings"]["setup_completed"])
        self.assertEqual(data["settings"]["song_codec"], "aac-legacy")
        self.assertEqual(data["settings"]["language"], "zh-CN")
        self.assertEqual(data["settings"]["theme"], "warm")
        self.assertFalse(data["settings"]["use_wrapper"])
        self.assertEqual(data["settings"]["wrapper_decrypt_ip"], "127.0.0.1:10022")
        self.assertEqual(data["settings"]["network_mode"], "auto")
        self.assertEqual(data["settings"]["proxy_url"], "")
        self.assertEqual(data["settings"]["artist_auto_select"], "")
        self.assertIn("wrapper_status", data)
        self.assertTrue(data["wrapper_status"]["message"])
        self.assertIn("runtime", data)
        self.assertEqual(data["runtime"]["platform"], platform.system())
        self.assertTrue(data["runtime"]["folder_picker_supported"])
        self.assertTrue(data["runtime"]["file_picker_supported"])
        self.assertEqual(
            data["runtime"]["conversion_supported"],
            resolve_executable("ffmpeg").available,
        )
        self.assertEqual(
            data["runtime"]["file_actions_supported"],
            self.server.file_actions.supported,
        )

    def test_cancel_queued_job_marks_it_cancelled(self):
        self.server.auth_manager.login_with_webview()
        with patch.object(self.server.job_manager, "job_queue", queue.Queue()):
            created = self.server.job_manager.create_job(
                {
                    "url_text": "https://music.apple.com/us/album/test/123456789?i=123456790",
                    "output_path": str(self.paths.app_support_dir / "downloads"),
                    "overwrite": False,
                    "save_cover": True,
                    "log_level": "INFO",
                }
            )

        response = self._post_json(f"/api/jobs/{created.id}/cancel", {})
        self.assertTrue(response["ok"])
        job = self._get_json(f"/api/jobs/{created.id}")
        self.assertEqual(job["status"], "cancelled")
        self.assertTrue(job["cancel_requested"])
        self.assertIn("任务已取消（尚未开始执行）。", "\n".join(job["logs"]))

    def test_cancel_running_download_job_stops_with_cancelled_status(self):
        self.server.auth_manager.login_with_webview()

        def fake_run_sync(service, _job):
            result = DownloadResult(output_path=str(self.paths.app_support_dir / "downloads"))
            deadline = time.time() + 2
            while time.time() < deadline:
                if service.cancel_callback and service.cancel_callback():
                    result.finished_at = time.time()
                    raise JobCancelledError(result.to_dict())
                time.sleep(0.02)
            self.fail("cancel callback was not triggered")

        with patch("gamdl.web_gui.DownloadService.run_sync", autospec=True, side_effect=fake_run_sync):
            created = self._post_json(
                "/api/jobs",
                {
                    "url_text": "https://music.apple.com/us/album/test/123456789?i=123456790",
                    "output_path": str(self.paths.app_support_dir / "downloads"),
                    "overwrite": False,
                    "save_cover": True,
                    "log_level": "INFO",
                },
            )
            deadline = time.time() + 2
            while time.time() < deadline:
                running_job = self._get_json(f"/api/jobs/{created['id']}")
                if running_job["status"] == "running":
                    break
                time.sleep(0.02)
            else:
                self.fail("job did not enter running state")

            response = self._post_json(f"/api/jobs/{created['id']}/cancel", {})
            self.assertTrue(response["ok"])
            job = self._wait_for_terminal_job(created["id"], timeout=2.5)

        self.assertEqual(job["status"], "cancelled")
        self.assertEqual(job["return_code"], 130)
        self.assertTrue(job["cancel_requested"])
        logs = "\n".join(job["logs"])
        self.assertIn("已收到取消请求，取消中，当前文件完成后停止。", logs)
        self.assertIn("任务已停止。", logs)

    def test_conversion_service_terminates_ffmpeg_on_cancel(self):
        service = ConversionService(cancel_callback=lambda: True)
        result = ConversionResult()

        class FakeProcess:
            def __init__(self) -> None:
                self.terminated = False
                self.killed = False
                self.poll_count = 0

            def poll(self):
                self.poll_count += 1
                return None

            def communicate(self, timeout=None):
                return ("", "")

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        process = FakeProcess()
        with patch("gamdl.app.conversion.subprocess.Popen", return_value=process):
            with self.assertRaises(JobCancelledError):
                service._run_ffmpeg(
                    Path("/tmp/in.m4a"),
                    Path("/tmp/out.mp3"),
                    ConversionFormat.MP3,
                    44100,
                    False,
                    result,
                )

        self.assertTrue(process.terminated)

    def test_convert_path_reraises_job_cancelled_error(self):
        service = ConversionService()
        result = ConversionResult()

        with (
            patch.object(service, "_read_metadata", return_value=SimpleNamespace(sample_rate=44100)),
            patch.object(
                service,
                "_run_ffmpeg",
                side_effect=JobCancelledError(result.to_dict()),
            ),
            patch.object(service, "_write_metadata"),
        ):
            with self.assertRaises(JobCancelledError):
                service._convert_path(
                    Path("/tmp/in.m4a"),
                    Path("/tmp/out.mp3"),
                    ConversionFormat.MP3,
                    False,
                    result,
                )

        self.assertEqual(result.errors, 0)

    def test_login_webview_uses_selected_browser(self):
        data = self._post_json("/api/auth/login-webview", {"browser": "chrome"})

        self.assertTrue(data["session"]["connected"])
        self.assertEqual(data["session"]["login_method"], "browser-import")
        self.assertEqual(data["session"]["browser"], "chrome")

    def test_login_webview_uses_configured_metadata_language(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self.server.settings_store.save(
            {
                "output_path": str(remembered_path),
                "language": "ja-JP",
            }
        )

        data = self._post_json("/api/auth/login-webview", {"browser": "chrome"})

        self.assertEqual(data["session"]["language"], "ja-JP")

    def test_login_webview_prefers_request_metadata_language_over_saved_setting(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self.server.settings_store.save(
            {
                "output_path": str(remembered_path),
                "language": "zh-CN",
            }
        )

        data = self._post_json(
            "/api/auth/login-webview",
            {"browser": "chrome", "language": "ja_jp"},
        )

        self.assertEqual(data["session"]["language"], "ja-JP")

    def test_import_browser_uses_configured_metadata_language(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self.server.settings_store.save(
            {
                "output_path": str(remembered_path),
                "language": "zh-HK",
            }
        )

        data = self._post_json("/api/auth/import-browser", {"browser": "chrome"})

        self.assertEqual(data["session"]["language"], "zh-HK")

    def test_import_browser_prefers_request_metadata_language_over_saved_setting(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self.server.settings_store.save(
            {
                "output_path": str(remembered_path),
                "language": "zh-CN",
            }
        )

        data = self._post_json(
            "/api/auth/import-browser",
            {"browser": "chrome", "language": "en-us"},
        )

        self.assertEqual(data["session"]["language"], "en-US")

    def test_login_webview_rejects_invalid_request_metadata_language(self):
        status, data = self._post_json_error(
            "/api/auth/login-webview",
            {"browser": "chrome", "language": "zhcn"},
        )

        self.assertEqual(status, 400)
        self.assertEqual(data["category"], "settings")
        self.assertIn("元数据语言格式无效", data["error"])

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
                "language": "ja_jp",
                "theme": "cool",
                "network_mode": "custom",
                "proxy_url": "http://127.0.0.1:7890",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "artist_auto_select": "top-songs",
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
        self.assertEqual(data["settings"]["language"], "ja-JP")
        self.assertEqual(data["settings"]["theme"], "cool")
        self.assertEqual(data["settings"]["network_mode"], "custom")
        self.assertEqual(data["settings"]["proxy_url"], "http://127.0.0.1:7890")
        self.assertFalse(data["settings"]["use_wrapper"])
        self.assertEqual(data["settings"]["wrapper_decrypt_ip"], "127.0.0.1:10022")
        self.assertEqual(data["settings"]["artist_auto_select"], "")
        self.assertEqual(data["settings"]["open_file_application"], "VLC")
        self.assertNotIn("unknown_field", data["settings"])

    def test_validate_settings_output_path_creates_folder(self):
        target = self.paths.app_support_dir / "chosen-downloads"
        data = self._post_json("/api/settings/validate", {"output_path": str(target)})
        self.assertEqual(data["output_path"], str(target))
        self.assertTrue(target.exists())

    def test_post_settings_rejects_invalid_metadata_language(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        status, data = self._post_json_error(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "language": "zhcn",
            },
        )

        self.assertEqual(status, 400)
        self.assertIn("元数据语言格式无效", data["error"])
        self.assertEqual(data["category"], "settings")

    def test_post_settings_accepts_short_metadata_language(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        data = self._post_json(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "language": "en",
            },
        )

        self.assertEqual(data["settings"]["language"], "en")

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

    def test_post_settings_rejects_empty_custom_proxy(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        status, payload = self._post_json_error(
            "/api/settings",
            {
                "output_path": str(remembered_path),
                "network_mode": "custom",
                "proxy_url": "",
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(payload["category"], "network")
        self.assertIn("必须填写代理地址", payload["error"])

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

    def test_start_wrapper_uses_wrapper_manager_and_returns_status(self):
        with (
            patch.object(
                self.server.wrapper_manager,
                "ensure_running",
                return_value="127.0.0.1:10022",
            ) as ensure_running,
            patch.object(
                self.server.wrapper_manager,
                "probe_status",
                return_value=SimpleNamespace(
                    available=True,
                    mode="docker",
                    resolved_ip="127.0.0.1:10022",
                    message="已检测到通过 Docker 运行的外部 wrapper。",
                ),
            ) as probe_status,
        ):
            data = self._post_json(
                "/api/wrapper/start",
                {"wrapper_decrypt_ip": "127.0.0.1:10022"},
            )

        ensure_running.assert_called_once()
        self.assertEqual(ensure_running.call_args.args[0], "127.0.0.1:10022")
        self.assertEqual(ensure_running.call_args.args[1].mode, "auto")
        probe_status.assert_called_once()
        self.assertEqual(probe_status.call_args.args[0], "127.0.0.1:10022")
        self.assertEqual(probe_status.call_args.args[1].mode, "auto")
        self.assertTrue(data["ok"])
        self.assertEqual(data["resolved_ip"], "127.0.0.1:10022")
        self.assertTrue(data["wrapper_status"]["available"])

    def test_start_wrapper_uses_current_network_form_values(self):
        remembered_path = self.paths.app_support_dir / "remembered"
        self.server.settings_store.save(
            {
                "output_path": str(remembered_path),
                "network_mode": "auto",
                "proxy_url": "",
            }
        )

        with (
            patch.object(
                self.server.wrapper_manager,
                "ensure_running",
                return_value="127.0.0.1:10022",
            ) as ensure_running,
            patch.object(
                self.server.wrapper_manager,
                "probe_status",
                return_value=SimpleNamespace(
                    available=True,
                    mode="docker",
                    resolved_ip="127.0.0.1:10022",
                    message="已检测到通过 Docker 运行的外部 wrapper。",
                ),
            ),
        ):
            self._post_json(
                "/api/wrapper/start",
                {
                    "wrapper_decrypt_ip": "127.0.0.1:10022",
                    "network_mode": "custom",
                    "proxy_url": "http://127.0.0.1:7890",
                },
            )

        self.assertEqual(ensure_running.call_args.args[1].mode, "custom")
        self.assertEqual(ensure_running.call_args.args[1].proxy_url, "http://127.0.0.1:7890")

    def test_start_wrapper_rejects_non_loopback_host(self):
        code, payload = self._post_json_error(
            "/api/wrapper/start",
            {"wrapper_decrypt_ip": "203.0.113.10:10022"},
        )

        self.assertEqual(code, 400)
        self.assertEqual(payload["category"], "filesystem")
        self.assertIn("本机回环地址", payload["error"])

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
            "https://github.com/Mrgu2/gu_music_downloader",
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

    def test_auth_import_browser_rejected_when_disabled(self):
        self.server.settings_store.save({"browser_import_enabled": False})

        status, data = self._post_json_error("/api/auth/import-browser", {"browser": "chrome"})

        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "当前已关闭浏览器导入功能。请先在设置中重新开启。")

    def test_diagnostics_export_creates_zip(self):
        data = self._post_json("/api/diagnostics/export", {})
        bundle_path = Path(data["bundle_path"])
        self.assertTrue(bundle_path.exists())
        self.assertEqual(bundle_path.suffix, ".zip")

    def test_diagnostics_export_redacts_proxy_credentials_and_tokens(self):
        self.server.settings_store.save(
            {
                "output_path": str(self.paths.default_output_path),
                "theme": "cool",
                "network_mode": "custom",
                "proxy_url": "http://alice:secret@127.0.0.1:7890",
                "open_file_application": "VLC",
                "browser_import_enabled": False,
                "last_login_method": "browser-import",
                "song_codec": "alac",
                "language": "zh-TW",
                "use_wrapper": True,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
            }
        )
        self.server.log_store.channels["app"].append("Token: dev-secret-token")
        self.server.log_store.channels["app"].append("token: lower-secret-token")
        self.server.log_store.channels["app"].append("Authorization: bearer lower-bearer-token")
        self.server.log_store.channels["auth"].append('media-user-token=auth-secret-token')
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.logs_dir / "app.log").write_text(
            "authorization: Bearer bearer-secret-token and Bearer second-secret-token\nAuthorization: bearer third-secret-token\nToken: one token: two",
            encoding="utf-8",
        )

        data = self._post_json("/api/diagnostics/export", {})
        bundle_path = Path(data["bundle_path"])

        with zipfile.ZipFile(bundle_path) as archive:
            settings_payload = json.loads(archive.read("settings.json").decode("utf-8"))
            in_memory_logs = json.loads(archive.read("logs/in-memory.json").decode("utf-8"))
            app_log = archive.read("logs/app.log").decode("utf-8")

        self.assertEqual(settings_payload["proxy_url"], "http://***:***@127.0.0.1:7890")
        self.assertEqual(
            set(settings_payload),
            {
                "log_level",
                "browser_import_enabled",
                "last_login_method",
                "language",
                "song_codec",
                "use_wrapper",
                "wrapper_decrypt_ip",
                "network_mode",
                "proxy_url",
            },
        )
        self.assertNotIn("output_path", settings_payload)
        self.assertNotIn("theme", settings_payload)
        self.assertNotIn("open_file_application", settings_payload)
        self.assertNotIn("secret", json.dumps(in_memory_logs, ensure_ascii=False))
        self.assertNotIn("lower-bearer-token", json.dumps(in_memory_logs, ensure_ascii=False))
        self.assertIn("Token: ***", json.dumps(in_memory_logs, ensure_ascii=False))
        self.assertIn("media-user-token=***", json.dumps(in_memory_logs, ensure_ascii=False))
        self.assertNotIn("bearer-secret-token", app_log)
        self.assertNotIn("second-secret-token", app_log)
        self.assertNotIn("third-secret-token", app_log)
        self.assertNotIn("token: two", app_log.lower())
        self.assertIn("Bearer ***", app_log)

    def test_diagnostics_export_skips_symlinked_log_files(self):
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)
        secret_path = self.paths.app_support_dir / "secret.txt"
        secret_path.write_text("do-not-export", encoding="utf-8")
        symlink_path = self.paths.logs_dir / "symlink.log"
        try:
            symlink_path.symlink_to(secret_path)
        except OSError as exc:
            self.skipTest(f"symlink creation is unavailable: {exc}")

        data = self._post_json("/api/diagnostics/export", {})
        bundle_path = Path(data["bundle_path"])

        with zipfile.ZipFile(bundle_path) as archive:
            names = set(archive.namelist())
            archive_payload = {
                name: archive.read(name).decode("utf-8", errors="replace")
                for name in names
            }

        self.assertNotIn("logs/symlink.log", names)
        self.assertNotIn("do-not-export", json.dumps(archive_payload, ensure_ascii=False))

    def test_logs_endpoint_returns_folder_path(self):
        data = self._get_json("/api/logs")

        self.assertIn("channels", data)
        self.assertEqual(data["folder_path"], str(self.paths.logs_dir))

    def test_sensitive_get_rejects_missing_local_request_token(self):
        status, body, _headers = self._request("/api/settings", include_token=False)
        self.assertEqual(status, 403)
        self.assertIn("需要有效本地会话令牌", body.decode("utf-8"))

    def test_open_logs_folder_uses_logs_directory(self):
        self.paths.logs_dir.mkdir(parents=True, exist_ok=True)

        data = self._post_json("/api/logs/open-folder", {})

        self.assertTrue(data["ok"])
        self.assertEqual(data["folder_path"], str(self.paths.logs_dir))
        self.assertEqual(
            self.server.file_actions.calls[-1],
            ("open_output", str(self.paths.logs_dir)),
        )

    def test_create_job_requires_logged_in_session(self):
        self.server.auth_manager.logout()

        status, data = self._post_json_error(
            "/api/jobs",
            {
                "url_text": "https://music.apple.com/us/album/test/123456789?i=123456790",
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "log_level": "INFO",
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "请先完成 Apple Music 登录。")

    def test_logout_returns_error_when_token_delete_fails(self):
        with patch.object(
            self.server.auth_manager,
            "logout",
            side_effect=TokenDeletionError("当前系统凭据存储不可用，无法安全清除 Apple Music 登录态。请启用系统钥匙串后重试。"),
        ):
            status, data = self._post_json_error("/api/auth/logout", {})

        self.assertEqual(status, 400)
        self.assertEqual(data["category"], "login")
        self.assertIn("无法安全清除 Apple Music 登录态", data["error"])

    def test_create_job_rejects_artist_without_selection(self):
        self.server.auth_manager.login_with_webview()

        status, data = self._post_json_error(
            "/api/jobs",
            {
                "url_text": "https://music.apple.com/us/artist/test/123456789",
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "log_level": "INFO",
                "artist_auto_select": "",
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "检测到艺术家链接，请先选择要下载的艺术家内容。")

    def test_create_job_accepts_artist_with_selection(self):
        self.server.auth_manager.login_with_webview()

        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(
                downloaded_items=2,
                output_path=str(self.paths.app_support_dir / "downloads"),
            ),
        ):
            data = self._post_json(
                "/api/jobs",
                {
                    "url_text": "https://music.apple.com/us/artist/test/123456789",
                    "output_path": str(self.paths.app_support_dir / "downloads"),
                    "overwrite": False,
                    "save_cover": True,
                    "log_level": "INFO",
                    "artist_auto_select": "top-songs",
                },
            )
            for _ in range(30):
                job = self._get_json(f"/api/jobs/{data['id']}")
                if job["status"] in {"completed", "failed", "cancelled"}:
                    break
                time.sleep(0.05)

        self.assertEqual(job["payload"]["artist_auto_select"], "top-songs")
        self.assertEqual(job["url_preview"][0]["kind"], "artist")
        self.assertEqual(job["status"], "completed")

        settings = self._get_json("/api/settings")
        self.assertEqual(settings["settings"]["artist_auto_select"], "")

    def test_create_job_rejects_wrapper_required_codec_when_wrapper_disabled(self):
        self.server.auth_manager.login_with_webview()

        status, data = self._post_json_error(
            "/api/jobs",
            {
                "url_text": "https://music.apple.com/us/album/test/123456789?i=123456790",
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "log_level": "INFO",
                "song_codec": "alac",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
            },
        )

        self.assertEqual(status, 400)
        self.assertEqual(
            data["error"],
            "当前所选音质需要启用外部 wrapper。请先打开“启用 wrapper”，或切回 AAC。",
        )

    def test_create_job_uses_internal_download_service(self):
        self.server.auth_manager.login_with_webview()
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

    def test_failed_download_job_records_error_result(self):
        self.server.auth_manager.login_with_webview()
        downloads_path = self.paths.app_support_dir / "downloads"
        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            side_effect=RuntimeError("尚未登录 Apple Music。"),
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

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], "尚未登录 Apple Music。")
        self.assertEqual(job["result"]["errors"], 1)
        self.assertEqual(job["result"]["downloaded_items"], 0)
        self.assertEqual(job["result"]["output_path"], str(downloads_path))

    def test_failed_download_job_invalid_auth_invalidates_session(self):
        self.server.auth_manager.login_with_webview()
        downloads_path = self.paths.app_support_dir / "downloads"
        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            side_effect=ApiError(
                '{"errors":[{"title":"Forbidden","detail":"Invalid authentication","status":"403","code":"40300"}]}',
                403,
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

        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["error_message"], "Apple Music 登录已失效，请重新登录。")
        self.assertEqual(job["error_category"], "login")
        self.assertEqual(job["result"]["errors"], 1)
        self.assertFalse(self.server.auth_manager.get_session_status().connected)
        self.assertEqual(
            self.server.auth_manager.get_session_status().last_error,
            "Apple Music 登录已失效，请重新登录。",
        )

    def test_retry_failed_job_requeues_retryable_urls(self):
        self.server.auth_manager.login_with_webview()
        failed_job = Job(
            id="job-retry-failed",
            urls=["https://music.apple.com/us/album/test/1"],
            url_preview=[{"kind": "album", "supported": True, "valid": True}],
            payload={
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "log_level": "INFO",
                "song_codec": "aac-legacy",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "artist_auto_select": "",
            },
            command=[],
            status="failed",
            result={
                "errors": 1,
                "failed_items": [
                    {
                        "title": "Track 1",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "url",
                            "url": "https://music.apple.com/us/song/test-song/111",
                        },
                        "error": "boom",
                    },
                    {
                        "title": "Track 2",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "url",
                            "url": "https://music.apple.com/us/song/test-song/111",
                        },
                        "error": "boom",
                    },
                    {
                        "title": "Track 3",
                        "retry_url": "https://music.apple.com/us/song/test-song/222",
                        "retry_target": {
                            "strategy": "url",
                            "url": "https://music.apple.com/us/song/test-song/222",
                        },
                        "error": "boom",
                    },
                ],
            },
        )
        self._store_job(failed_job)

        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(downloaded_items=0, skipped_items=2),
        ):
            data = self._post_json("/api/jobs/job-retry-failed/retry-failed", {})
            self._wait_for_terminal_job(data["retry_job"]["id"])
        retry_job = self._get_json(f"/api/jobs/{data['retry_job']['id']}")

        self.assertTrue(data["ok"])
        self.assertEqual(data["retry_count"], 2)
        self.assertEqual(
            retry_job["urls"],
            [
                "https://music.apple.com/us/song/test-song/111",
                "https://music.apple.com/us/song/test-song/222",
            ],
        )
        self.assertEqual(
            retry_job["payload"]["retry_items"],
            [
                {
                    "title": "Track 1",
                    "kind": None,
                    "source_url": None,
                    "strategy": "url",
                    "url": "https://music.apple.com/us/song/test-song/111",
                },
                {
                    "title": "Track 3",
                    "kind": None,
                    "source_url": None,
                    "strategy": "url",
                    "url": "https://music.apple.com/us/song/test-song/222",
                },
            ],
        )

    def test_create_download_job_preserves_task_level_save_playlist(self):
        self.server.auth_manager.login_with_webview()
        downloads_path = self.paths.app_support_dir / "downloads"

        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(downloaded_items=0, skipped_items=1),
        ):
            data = self._post_json(
                "/api/jobs",
                {
                    "url_text": "https://music.apple.com/us/playlist/test/pl.u-abcdef123",
                    "output_path": str(downloads_path),
                    "overwrite": False,
                    "save_cover": True,
                    "save_playlist": True,
                    "log_level": "INFO",
                    "song_codec": "aac-legacy",
                    "use_wrapper": False,
                    "wrapper_decrypt_ip": "127.0.0.1:10022",
                },
            )
            self._wait_for_terminal_job(data["id"])

        job = self._get_json(f"/api/jobs/{data['id']}")
        self.assertTrue(job["payload"]["save_playlist"])

    def test_retry_failed_job_preserves_playlist_retry_context(self):
        self.server.auth_manager.login_with_webview()
        failed_job = Job(
            id="job-retry-playlist",
            urls=["https://music.apple.com/us/playlist/test/pl.1"],
            url_preview=[{"kind": "playlist", "supported": True, "valid": True}],
            payload={
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "save_playlist": True,
                "log_level": "INFO",
                "song_codec": "aac-legacy",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "artist_auto_select": "",
            },
            command=[],
            status="failed",
            result={
                "errors": 2,
                "failed_items": [
                    {
                        "title": "Track 1",
                        "kind": "song",
                        "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "playlist-track",
                            "song_url": "https://music.apple.com/us/song/test-song/111",
                            "playlist_tags": {
                                "playlist_artist": "Foo",
                                "playlist_title": "Bar",
                                "playlist_id": 123,
                                "playlist_track": 1,
                            },
                        },
                        "error": "boom",
                    },
                    {
                        "title": "Track 1 duplicate",
                        "kind": "song",
                        "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "playlist-track",
                            "song_url": "https://music.apple.com/us/song/test-song/111",
                            "playlist_tags": {
                                "playlist_artist": "Foo",
                                "playlist_title": "Bar",
                                "playlist_id": 123,
                                "playlist_track": 1,
                            },
                        },
                        "error": "boom",
                    },
                    {
                        "title": "Track 1 second slot",
                        "kind": "song",
                        "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "playlist-track",
                            "song_url": "https://music.apple.com/us/song/test-song/111",
                            "playlist_tags": {
                                "playlist_artist": "Foo",
                                "playlist_title": "Bar",
                                "playlist_id": 123,
                                "playlist_track": 3,
                            },
                        },
                        "error": "boom",
                    },
                ],
            },
        )
        self._store_job(failed_job)

        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(downloaded_items=0, skipped_items=1),
        ):
            data = self._post_json("/api/jobs/job-retry-playlist/retry-failed", {})
            self._wait_for_terminal_job(data["retry_job"]["id"])
        retry_job = self._get_json(f"/api/jobs/{data['retry_job']['id']}")

        self.assertTrue(data["ok"])
        self.assertEqual(data["retry_count"], 2)
        self.assertEqual(
            retry_job["payload"]["retry_items"],
            [
                {
                    "title": "Track 1",
                    "kind": "song",
                    "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                    "strategy": "playlist-track",
                    "song_url": "https://music.apple.com/us/song/test-song/111",
                    "playlist_tags": {
                        "playlist_artist": "Foo",
                        "playlist_title": "Bar",
                        "playlist_id": 123,
                        "playlist_track": 1,
                    },
                },
                {
                    "title": "Track 1 second slot",
                    "kind": "song",
                    "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                    "strategy": "playlist-track",
                    "song_url": "https://music.apple.com/us/song/test-song/111",
                    "playlist_tags": {
                        "playlist_artist": "Foo",
                        "playlist_title": "Bar",
                        "playlist_id": 123,
                        "playlist_track": 3,
                    },
                },
            ],
        )

    def test_retry_failed_job_preserves_artist_top_songs_retry_context(self):
        self.server.auth_manager.login_with_webview()
        failed_job = Job(
            id="job-retry-artist-top-songs",
            urls=["https://music.apple.com/us/artist/test/1"],
            url_preview=[{"kind": "artist", "supported": True, "valid": True}],
            payload={
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "save_playlist": False,
                "log_level": "INFO",
                "song_codec": "aac-legacy",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "artist_auto_select": "top-songs",
            },
            command=[],
            status="failed",
            result={
                "errors": 1,
                "failed_items": [
                    {
                        "title": "Track 1",
                        "kind": "song",
                        "source_url": "https://music.apple.com/us/artist/test/1",
                        "retry_url": "https://music.apple.com/us/song/test-song/111",
                        "retry_target": {
                            "strategy": "song-context",
                            "song_url": "https://music.apple.com/us/song/test-song/111",
                            "source_context": "artist-top-songs",
                            "artist_folder_name": "Artist A",
                        },
                        "error": "boom",
                    }
                ],
            },
        )
        self._store_job(failed_job)

        with patch(
            "gamdl.web_gui.DownloadService.run_sync",
            return_value=FakeDownloadResult(downloaded_items=0, skipped_items=1),
        ):
            data = self._post_json("/api/jobs/job-retry-artist-top-songs/retry-failed", {})
            self._wait_for_terminal_job(data["retry_job"]["id"])
        retry_job = self._get_json(f"/api/jobs/{data['retry_job']['id']}")

        self.assertTrue(data["ok"])
        self.assertEqual(data["retry_count"], 1)
        self.assertEqual(
            retry_job["payload"]["retry_items"],
            [
                {
                    "title": "Track 1",
                    "kind": "song",
                    "source_url": "https://music.apple.com/us/artist/test/1",
                    "strategy": "song-context",
                    "song_url": "https://music.apple.com/us/song/test-song/111",
                    "source_context": "artist-top-songs",
                    "artist_folder_name": "Artist A",
                }
            ],
        )
        self.assertEqual(
            retry_job["urls"],
            [
                "https://music.apple.com/us/song/test-song/111",
            ],
        )

    def test_retry_failed_job_rejects_when_no_retryable_items(self):
        self.server.auth_manager.login_with_webview()
        failed_job = Job(
            id="job-no-retry",
            urls=["https://music.apple.com/us/album/test/1"],
            url_preview=[{"kind": "album", "supported": True, "valid": True}],
            payload={
                "output_path": str(self.paths.app_support_dir / "downloads"),
                "overwrite": False,
                "save_cover": True,
                "log_level": "INFO",
                "song_codec": "aac-legacy",
                "use_wrapper": False,
                "wrapper_decrypt_ip": "127.0.0.1:10022",
                "artist_auto_select": "",
            },
            command=[],
            status="failed",
            result={"errors": 1, "failed_items": [{"title": "Track 1", "retry_url": None}]},
        )
        self._store_job(failed_job)

        status, data = self._post_json_error("/api/jobs/job-no-retry/retry-failed", {})

        self.assertEqual(status, 400)
        self.assertEqual(data["error"], "当前任务没有可重试的失败歌曲。")

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
        self.server.settings_store.save(
            {
                "output_path": str(self.paths.default_output_path),
                "open_file_application": "VLC",
            }
        )
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

    def test_open_latest_file_action_rejects_unlisted_application(self):
        latest_media_path = self.paths.default_output_path / "album" / "Song.m4a"
        latest_media_path.parent.mkdir(parents=True, exist_ok=True)
        latest_media_path.write_text("audio", encoding="utf-8")
        job = Job(
            id="job-open-file-unlisted-app",
            urls=["https://music.apple.com/us/album/test/1?i=2"],
            url_preview=[],
            payload={"output_path": str(latest_media_path.parent)},
            command=[],
            status="completed",
            result={"latest_media_path": str(latest_media_path)},
        )
        self._store_job(job)

        status, body, _headers = self._request(
            "/api/jobs/job-open-file-unlisted-app/open-latest-file",
            method="POST",
            payload={"application_path": "/tmp/not-from-menu"},
        )
        data = json.loads(body.decode("utf-8"))

        self.assertEqual(status, 400)
        self.assertEqual(data["error"], INVALID_OPEN_APPLICATION_ERROR)
        self.assertEqual(self.server.file_actions.calls, [])

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

        status, _body, _headers = self._request("/api/jobs/job-open-with-missing/open-with-options")
        self.assertEqual(status, 400)

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
                patch(
                    "gamdl.app.downloads.AppleMusicApi.create",
                    new=AsyncMock(return_value=fake_api),
                ) as create_api,
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
            self.assertEqual(create_api.await_args.kwargs["language"], "zh-CN")
            self.assertTrue(paths.temp_dir.exists())
        finally:
            tempdir.cleanup()

    def test_create_downloader_passes_configured_metadata_language(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_api = type(
                "FakeApi",
                (),
                {
                    "active_subscription": True,
                    "storefront": "jp",
                    "language": "ja-JP",
                },
            )()

            with (
                patch(
                    "gamdl.app.downloads.AppleMusicApi.create",
                    new=AsyncMock(return_value=fake_api),
                ) as create_api,
                patch("gamdl.app.downloads.ItunesApi"),
                patch("gamdl.app.downloads.AppleMusicInterface"),
                patch("gamdl.app.downloads.AppleMusicSongInterface"),
                patch("gamdl.app.downloads.AppleMusicSongDownloader"),
                patch("gamdl.app.downloads.AppleMusicDownloader"),
                patch("gamdl.app.downloads.AppleMusicBaseDownloader"),
            ):
                asyncio.run(
                    service._create_downloader(
                        DownloadJob(
                            urls=["https://music.apple.com/jp/album/test/1?i=2"],
                            output_path="/tmp/downloads",
                            language="ja-JP",
                        )
                    )
                )

            self.assertEqual(create_api.await_args.kwargs["language"], "ja-JP")
        finally:
            tempdir.cleanup()

    def test_create_downloader_passes_artist_auto_select(self):
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
                patch("gamdl.app.downloads.AppleMusicBaseDownloader"),
                patch("gamdl.app.downloads.AppleMusicDownloader") as downloader_cls,
            ):
                asyncio.run(
                    service._create_downloader(
                        DownloadJob(
                            urls=["https://music.apple.com/us/artist/test/1"],
                            output_path="/tmp/downloads",
                            artist_auto_select="top-songs",
                        )
                    )
                )

            self.assertEqual(
                downloader_cls.call_args.kwargs["artist_auto_select"].value,
                "top-songs",
            )
        finally:
            tempdir.cleanup()

    def test_create_downloader_closes_api_when_subscription_check_fails(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_api = type(
                "FakeApi",
                (),
                {
                    "active_subscription": False,
                    "storefront": "us",
                    "language": "zh-CN",
                    "close": AsyncMock(),
                },
            )()

            with patch("gamdl.app.downloads.AppleMusicApi.create", new=AsyncMock(return_value=fake_api)):
                with self.assertRaisesRegex(RuntimeError, "没有可用的 Apple Music 订阅"):
                    asyncio.run(
                        service._create_downloader(
                            DownloadJob(
                                urls=["https://music.apple.com/us/album/test/1?i=2"],
                                output_path="/tmp/downloads",
                            )
                        )
                    )

            fake_api.close.assert_awaited_once()
        finally:
            tempdir.cleanup()

    def test_run_marks_album_track_url_failures_as_retryable_song(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            track_url = "https://music.apple.com/us/album/test/1?i=123"

            class SongUrlFailureDownloader:
                interface = type(
                    "FakeInterface",
                    (),
                    {"apple_music_api": type("FakeApi", (), {"storefront": "us"})()},
                )()

                def get_url_info(self, _url):
                    return FakeUrlInfo(type="album", id="1", sub_id="123")

                async def get_download_queue(self, _url_info):
                    raise RuntimeError("boom")

            with patch.object(
                service,
                "_create_downloader",
                new=AsyncMock(return_value=SongUrlFailureDownloader()),
            ):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=[track_url],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(result.failed_items[0]["kind"], "song")
            self.assertEqual(result.failed_items[0]["retry_url"], track_url)
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

    def test_run_marks_album_url_failures_as_retryable_collection(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)

            class AlbumUrlFailureDownloader:
                interface = type(
                    "FakeInterface",
                    (),
                    {"apple_music_api": type("FakeApi", (), {"storefront": "us"})()},
                )()

                def get_url_info(self, _url):
                    return FakeUrlInfo(type="album")

                async def get_download_queue(self, _url_info):
                    raise RuntimeError("boom")

            with patch.object(
                service,
                "_create_downloader",
                new=AsyncMock(return_value=AlbumUrlFailureDownloader()),
            ):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/album/test/1"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/album/test/1",
            )
        finally:
            tempdir.cleanup()

    def test_run_falls_back_to_album_url_for_track_failures_without_song_url(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "missing" / "Track 1.m4a"),
                        media_type="song",
                        title="Track 1",
                        url=None,
                        error=Exception("boom"),
                    )
                ]
            )

            class AlbumTrackFailureDownloader(FakeDownloader):
                def get_url_info(self, _url):
                    return FakeUrlInfo(type="album")

            fake_downloader = AlbumTrackFailureDownloader(fake_downloader.queue_items)

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/album/test/1"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/album/test/1",
            )
        finally:
            tempdir.cleanup()

    def test_run_falls_back_to_artist_url_for_track_failures_without_song_url(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "missing" / "Track 1.m4a"),
                        media_type="song",
                        title="Track 1",
                        url=None,
                        error=Exception("boom"),
                    )
                ]
            )

            class ArtistTrackFailureDownloader(FakeDownloader):
                def get_url_info(self, _url):
                    return FakeUrlInfo(type="artist")

            fake_downloader = ArtistTrackFailureDownloader(fake_downloader.queue_items)

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/artist/test/1"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/artist/test/1",
            )
        finally:
            tempdir.cleanup()

    def test_run_preserves_artist_top_songs_context_for_track_failures(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "missing" / "Track 1.m4a"),
                        media_type="song",
                        title="Track 1",
                        url="https://music.apple.com/us/song/track-1/123",
                        source_context="artist-top-songs",
                        artist_folder_name="Artist A",
                        error=Exception("boom"),
                    )
                ]
            )

            class ArtistTrackFailureDownloader(FakeDownloader):
                def get_url_info(self, _url):
                    return FakeUrlInfo(type="artist")

            fake_downloader = ArtistTrackFailureDownloader(fake_downloader.queue_items)

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/artist/test/1"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                            artist_auto_select="top-songs",
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_target"],
                {
                    "strategy": "song-context",
                    "song_url": "https://music.apple.com/us/song/track-1/123",
                    "source_context": "artist-top-songs",
                    "artist_folder_name": "Artist A",
                },
            )
        finally:
            tempdir.cleanup()

    def test_run_falls_back_to_playlist_url_for_track_failures_without_song_url(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "missing" / "Track 1.m4a"),
                        media_type="song",
                        title="Track 1",
                        url=None,
                        error=Exception("boom"),
                    )
                ]
            )

            class PlaylistTrackFailureDownloader(FakeDownloader):
                def get_url_info(self, _url):
                    return FakeUrlInfo(type="playlist")

            fake_downloader = PlaylistTrackFailureDownloader(fake_downloader.queue_items)

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/playlist/test/pl.1"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/playlist/test/pl.1",
            )
        finally:
            tempdir.cleanup()

    def test_run_leaves_latest_media_empty_when_all_items_fail_or_skip(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            missing_path = Path(tempdir.name) / "missing" / "Track 1.m4a"
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(missing_path),
                        title="Track 1",
                        url="https://music.apple.com/us/song/track-1/123",
                        error=Exception("boom"),
                    )
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

            self.assertEqual(result.downloaded_items, 0)
            self.assertEqual(result.errors, 1)
            self.assertIsNone(result.latest_media_path)
            self.assertIsNone(result.latest_media_dir)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/song/track-1/123",
            )
        finally:
            tempdir.cleanup()

    def test_run_records_playlist_retry_context_for_failed_song(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            fake_downloader = FakeDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "missing" / "Track 1.m4a"),
                        title="Track 1",
                        url="https://music.apple.com/us/song/track-1/123",
                        playlist_metadata={
                            "type": "playlist",
                            "attributes": {"name": "Test Playlist"},
                        },
                        playlist_tags=PlaylistTags(
                            playlist_artist="Foo",
                            playlist_title="Test Playlist",
                            playlist_id=123,
                            playlist_track=2,
                        ),
                        error=Exception("boom"),
                    )
                ]
            )

            class PlaylistTrackFailureDownloader(FakeDownloader):
                def get_url_info(self, _url):
                    return FakeUrlInfo(type="playlist")

            fake_downloader = PlaylistTrackFailureDownloader(fake_downloader.queue_items)

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/playlist/test/pl.123"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/song/track-1/123",
            )
            self.assertEqual(
                result.failed_items[0]["retry_target"],
                {
                    "strategy": "playlist-track",
                    "song_url": "https://music.apple.com/us/song/track-1/123",
                    "playlist_tags": {
                        "playlist_artist": "Foo",
                        "playlist_id": 123,
                        "playlist_title": "Test Playlist",
                        "playlist_track": 2,
                    },
                },
            )
        finally:
            tempdir.cleanup()

    def test_run_surfaces_sidecar_failures_in_logs_and_failed_items(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            logs = []
            service = DownloadService(
                media_user_token="test-token",
                paths=paths,
                log_callback=logs.append,
            )

            class SidecarFailureDownloader(FakeDownloader):
                async def download(self, download_item):
                    download_item.sidecar_failures = [
                        {"artifact_kind": "cover", "error": "cover boom"},
                        {"artifact_kind": "playlist-file", "error": "playlist boom"},
                    ]
                    return None

            fake_downloader = SidecarFailureDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "done" / "Track 1.m4a"),
                        title="Track 1",
                        url="https://music.apple.com/us/song/track-1/123",
                    )
                ]
            )
            Path(fake_downloader.queue_items[0].final_path).parent.mkdir(parents=True, exist_ok=True)
            Path(fake_downloader.queue_items[0].final_path).write_text("audio", encoding="utf-8")

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/song/track-1/123"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 0)
            self.assertEqual(result.downloaded_items, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertTrue(result.failed_items[0]["warning_only"])
            self.assertEqual(
                result.failed_items[0]["retry_url"],
                "https://music.apple.com/us/song/track-1/123",
            )
            self.assertEqual(
                result.failed_items[0]["sidecar_failures"],
                [
                    {"artifact_kind": "cover", "error": "cover boom"},
                    {"artifact_kind": "playlist-file", "error": "playlist boom"},
                ],
            )
            self.assertTrue(any('辅助文件写入失败 "Track 1" [cover]: cover boom' in line for line in logs))
            self.assertTrue(any('辅助文件写入失败 "Track 1" [playlist-file]: playlist boom' in line for line in logs))
        finally:
            tempdir.cleanup()

    def test_run_surfaces_sidecar_failures_on_media_file_exists_skip(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            logs = []
            service = DownloadService(
                media_user_token="test-token",
                paths=paths,
                log_callback=logs.append,
            )

            class SidecarSkipDownloader(FakeDownloader):
                async def download(self, download_item):
                    download_item.sidecar_failures = [
                        {"artifact_kind": "lyrics", "error": "lyrics boom"},
                    ]
                    raise GamdlError("exists")

            fake_downloader = SidecarSkipDownloader(
                [
                    FakeDownloadItem(
                        final_path=str(Path(tempdir.name) / "done" / "Track 1.m4a"),
                        title="Track 1",
                        url="https://music.apple.com/us/song/track-1/123",
                    )
                ]
            )

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/song/track-1/123"],
                            output_path=str(Path(tempdir.name) / "downloads"),
                        )
                    )
                )

            self.assertEqual(result.errors, 0)
            self.assertEqual(result.skipped_items, 1)
            self.assertEqual(len(result.failed_items), 1)
            self.assertTrue(result.failed_items[0]["warning_only"])
            self.assertEqual(
                result.failed_items[0]["sidecar_failures"],
                [{"artifact_kind": "lyrics", "error": "lyrics boom"}],
            )
            self.assertTrue(any('辅助文件写入失败 "Track 1" [lyrics]: lyrics boom' in line for line in logs))
        finally:
            tempdir.cleanup()

    def test_run_retries_playlist_track_with_preserved_context(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            downloads_path = Path(tempdir.name) / "downloads"
            playlist_file_path = downloads_path / "Playlists" / "Bar.m3u8"
            song_url = "https://music.apple.com/us/song/test-song/111"
            playlist_tags = {
                "playlist_artist": "Foo",
                "playlist_title": "Bar",
                "playlist_id": 123,
                "playlist_track": 2,
            }
            song_metadata = {
                "id": "111",
                "type": "song",
                "attributes": {
                    "name": "Track 1",
                    "url": song_url,
                },
            }

            class RetrySongDownloader:
                def __init__(self):
                    self.calls = []

                async def get_download_item(
                    self,
                    song_metadata_arg,
                    playlist_metadata=None,
                    playlist_tags_override=None,
                ):
                    self.calls.append(
                        {
                            "song_metadata": song_metadata_arg,
                            "playlist_metadata": playlist_metadata,
                            "playlist_tags_override": playlist_tags_override,
                        }
                    )
                    final_path = downloads_path / "Tracks" / "Track 1.m4a"
                    return SimpleNamespace(
                        media_metadata=song_metadata_arg,
                        playlist_metadata=None,
                        playlist_tags=playlist_tags_override,
                        playlist_file_path=str(playlist_file_path),
                        final_path=str(final_path),
                        error=None,
                    )

            class RetryApi:
                storefront = "us"

                async def get_song(self, song_id):
                    return {"data": [{**song_metadata, "id": song_id}]}

            class RetryDownloader:
                def __init__(self):
                    self.interface = SimpleNamespace(apple_music_api=RetryApi())
                    self.song_downloader = RetrySongDownloader()

                def get_url_info(self, _url):
                    return FakeUrlInfo(type="song", id="111")

                async def download(self, download_item):
                    final_path = Path(download_item.final_path)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    final_path.write_text("audio", encoding="utf-8")
                    playlist_file = Path(download_item.playlist_file_path)
                    playlist_file.parent.mkdir(parents=True, exist_ok=True)
                    lines = playlist_file.read_text(encoding="utf-8").splitlines() if playlist_file.exists() else []
                    while len(lines) < download_item.playlist_tags.playlist_track:
                        lines.append("")
                    lines[download_item.playlist_tags.playlist_track - 1] = "Tracks/Track 1.m4a"
                    playlist_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            fake_downloader = RetryDownloader()

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/playlist/test/pl.1"],
                            output_path=str(downloads_path),
                            save_playlist=True,
                            retry_items=[
                                {
                                    "title": "Track 1",
                                    "kind": "song",
                                    "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                                    "strategy": "playlist-track",
                                    "song_url": song_url,
                                    "playlist_tags": playlist_tags,
                                }
                            ],
                        )
                    )
                )

            self.assertEqual(result.errors, 0)
            self.assertEqual(result.downloaded_items, 1)
            self.assertEqual(result.processed_urls, 1)
            self.assertEqual(
                fake_downloader.song_downloader.calls[0]["playlist_tags_override"],
                PlaylistTags(**playlist_tags),
            )
            self.assertEqual(
                playlist_file_path.read_text(encoding="utf-8").splitlines(),
                ["", "Tracks/Track 1.m4a"],
            )
        finally:
            tempdir.cleanup()

    def test_run_retries_artist_top_songs_track_with_preserved_context(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            downloads_path = Path(tempdir.name) / "downloads"
            song_url = "https://music.apple.com/us/song/test-song/111"
            song_metadata = {
                "id": "111",
                "type": "song",
                "attributes": {
                    "name": "Track 1",
                    "url": song_url,
                },
            }

            class RetryDownloader:
                def __init__(self):
                    self.interface = SimpleNamespace(apple_music_api=RetryApi())
                    self.calls = []

                def get_url_info(self, _url):
                    return FakeUrlInfo(type="song", id="111")

                async def get_single_download_item(
                    self,
                    song_metadata_arg,
                    playlist_metadata=None,
                    source_context=None,
                    artist_folder_name=None,
                ):
                    self.calls.append(
                        {
                            "song_metadata": song_metadata_arg,
                            "playlist_metadata": playlist_metadata,
                            "source_context": source_context,
                            "artist_folder_name": artist_folder_name,
                        }
                    )
                    final_path = downloads_path / "Artist A" / "Top Songs" / "Track 1 [111].m4a"
                    return SimpleNamespace(
                        media_metadata=song_metadata_arg,
                        playlist_metadata=None,
                        playlist_tags=None,
                        final_path=str(final_path),
                        source_context=source_context,
                        artist_folder_name=artist_folder_name,
                        error=None,
                        sidecar_failures=[],
                    )

                async def download(self, download_item):
                    final_path = Path(download_item.final_path)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    final_path.write_text("audio", encoding="utf-8")

            class RetryApi:
                storefront = "us"

                async def get_song(self, song_id):
                    return {"data": [{**song_metadata, "id": song_id}]}

            fake_downloader = RetryDownloader()

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/artist/test/1"],
                            output_path=str(downloads_path),
                            artist_auto_select="top-songs",
                            retry_items=[
                                {
                                    "title": "Track 1",
                                    "kind": "song",
                                    "source_url": "https://music.apple.com/us/artist/test/1",
                                    "strategy": "song-context",
                                    "song_url": song_url,
                                    "source_context": "artist-top-songs",
                                    "artist_folder_name": "Artist A",
                                }
                            ],
                        )
                    )
                )

            self.assertEqual(result.errors, 0)
            self.assertEqual(result.downloaded_items, 1)
            self.assertEqual(result.processed_urls, 1)
            self.assertEqual(
                fake_downloader.calls[0]["source_context"],
                "artist-top-songs",
            )
            self.assertEqual(
                fake_downloader.calls[0]["artist_folder_name"],
                "Artist A",
            )
            self.assertTrue(
                (downloads_path / "Artist A" / "Top Songs" / "Track 1 [111].m4a").exists()
            )
        finally:
            tempdir.cleanup()

    def test_run_retries_playlist_track_with_album_sub_id_link(self):
        tempdir = tempfile.TemporaryDirectory()
        try:
            paths = AppPaths(base_dir=Path(tempdir.name), app_name="GamdlTest")
            service = DownloadService(media_user_token="test-token", paths=paths)
            downloads_path = Path(tempdir.name) / "downloads"
            playlist_file_path = downloads_path / "Playlists" / "Bar.m3u8"
            song_url = "https://music.apple.com/us/album/test/1?i=111"
            playlist_tags = {
                "playlist_artist": "Foo",
                "playlist_title": "Bar",
                "playlist_id": 123,
                "playlist_track": 2,
            }
            song_metadata = {
                "id": "111",
                "type": "song",
                "attributes": {
                    "name": "Track 1",
                    "url": song_url,
                },
            }

            class RetrySongDownloader:
                def __init__(self):
                    self.calls = []

                async def get_download_item(
                    self,
                    song_metadata_arg,
                    playlist_metadata=None,
                    playlist_tags_override=None,
                ):
                    self.calls.append(
                        {
                            "song_metadata": song_metadata_arg,
                            "playlist_metadata": playlist_metadata,
                            "playlist_tags_override": playlist_tags_override,
                        }
                    )
                    final_path = downloads_path / "Tracks" / "Track 1.m4a"
                    return SimpleNamespace(
                        media_metadata=song_metadata_arg,
                        playlist_metadata=None,
                        playlist_tags=playlist_tags_override,
                        playlist_file_path=str(playlist_file_path),
                        final_path=str(final_path),
                        error=None,
                    )

            class RetryApi:
                storefront = "us"

                async def get_song(self, song_id):
                    return {"data": [{**song_metadata, "id": song_id}]}

            class RetryDownloader:
                def __init__(self):
                    self.interface = SimpleNamespace(apple_music_api=RetryApi())
                    self.song_downloader = RetrySongDownloader()

                def get_url_info(self, _url):
                    return FakeUrlInfo(type="album", id="1", sub_id="111")

                async def download(self, download_item):
                    final_path = Path(download_item.final_path)
                    final_path.parent.mkdir(parents=True, exist_ok=True)
                    final_path.write_text("audio", encoding="utf-8")
                    playlist_file = Path(download_item.playlist_file_path)
                    playlist_file.parent.mkdir(parents=True, exist_ok=True)
                    lines = playlist_file.read_text(encoding="utf-8").splitlines() if playlist_file.exists() else []
                    while len(lines) < download_item.playlist_tags.playlist_track:
                        lines.append("")
                    lines[download_item.playlist_tags.playlist_track - 1] = "Tracks/Track 1.m4a"
                    playlist_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

            fake_downloader = RetryDownloader()

            with patch.object(service, "_create_downloader", new=AsyncMock(return_value=fake_downloader)):
                result = asyncio.run(
                    service.run(
                        DownloadJob(
                            urls=["https://music.apple.com/us/playlist/test/pl.1"],
                            output_path=str(downloads_path),
                            save_playlist=True,
                            retry_items=[
                                {
                                    "title": "Track 1",
                                    "kind": "song",
                                    "source_url": "https://music.apple.com/us/playlist/test/pl.1",
                                    "strategy": "playlist-track",
                                    "song_url": song_url,
                                    "playlist_tags": playlist_tags,
                                }
                            ],
                        )
                    )
                )

            self.assertEqual(result.errors, 0)
            self.assertEqual(result.downloaded_items, 1)
            self.assertEqual(
                fake_downloader.song_downloader.calls[0]["playlist_tags_override"],
                PlaylistTags(**playlist_tags),
            )
            self.assertEqual(
                playlist_file_path.read_text(encoding="utf-8").splitlines(),
                ["", "Tracks/Track 1.m4a"],
            )
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
