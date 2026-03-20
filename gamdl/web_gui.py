from __future__ import annotations

import argparse
import json
import logging
import queue
import re
import secrets
import threading
import time
import webbrowser
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import __version__
from .app import (
    AppLogStore,
    AppPaths,
    AppSettingsStore,
    AuthManager,
    BrowserType,
    DesktopFileActions,
    DiagnosticsService,
    DownloadJob,
    DownloadService,
    WrapperManager,
    configure_app_logging,
)
from .app.settings import AppSettings
from .desktop_runtime import detect_desktop_runtime
from .downloader.constants import VALID_URL_PATTERN

logger = logging.getLogger("gamdl.app.web")

MAX_LOG_LINES = 4000
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
GUI_SETTINGS_DEFAULTS = AppSettingsStore(AppPaths()).defaults().__dict__
SUPPORTED_BROWSER_IMPORTS = [browser.value for browser in BrowserType]
SUPPORTED_DOWNLOAD_KINDS = {"song", "album", "playlist", "library-playlist", "library-albums"}
DEFAULT_WRAPPER_DECRYPT_IP = "127.0.0.1:10022"


def parse_url_input(text: str) -> list[str]:
    return [token.strip() for token in re.split(r"\s+", text) if token.strip()]


def classify_url(url: str) -> dict[str, Any]:
    match = VALID_URL_PATTERN.match(url)
    if not match:
        return {
            "url": url,
            "valid": False,
            "kind": "invalid",
            "label": "Invalid",
            "supported": False,
        }

    groups = match.groupdict()
    if groups.get("library_type") == "playlist":
        kind = "library-playlist"
    elif groups.get("library_type") == "albums":
        kind = "library-albums"
    elif groups.get("type") == "album" and groups.get("sub_id"):
        kind = "song"
    else:
        kind = groups.get("type") or groups.get("library_type") or "unknown"

    label_map = {
        "album": "Album",
        "artist": "Artist",
        "library-albums": "Library Albums",
        "library-playlist": "Library Playlist",
        "music-video": "Music Video",
        "playlist": "Playlist",
        "post": "Post",
        "song": "Song",
    }
    return {
        "url": url,
        "valid": True,
        "kind": kind,
        "label": label_map.get(kind, kind.title()),
        "storefront": groups.get("storefront") or groups.get("library_storefront"),
        "id": (
            groups.get("sub_id")
            if kind == "song" and groups.get("sub_id")
            else groups.get("id") or groups.get("library_id")
        ),
        "supported": kind in SUPPORTED_DOWNLOAD_KINDS,
    }


def build_download_command(payload: dict[str, Any]) -> list[str]:
    cmd = [payload.get("runner", "internal-download")]
    for key in (
        "output_path",
        "overwrite",
        "save_cover",
        "log_level",
        "song_codec",
        "use_wrapper",
        "wrapper_decrypt_ip",
    ):
        if key in payload:
            cmd.append(f"{key}={payload[key]}")
    cmd.extend(payload.get("urls", []))
    return cmd


@dataclass
class Job:
    id: str
    urls: list[str]
    url_preview: list[dict[str, Any]]
    payload: dict[str, Any]
    command: list[str]
    status: str = "queued"
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    return_code: int | None = None
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error_category: str | None = None
    error_message: str | None = None

    def append_log(self, line: str) -> None:
        if line:
            self.logs.append(line)
            if len(self.logs) > MAX_LOG_LINES:
                self.logs = self.logs[-MAX_LOG_LINES:]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class JobManager:
    def __init__(
        self,
        auth_manager: AuthManager,
        settings_store: AppSettingsStore,
        paths: AppPaths,
    ) -> None:
        self.auth_manager = auth_manager
        self.settings_store = settings_store
        self.paths = paths
        self.jobs: dict[str, Job] = {}
        self.jobs_lock = threading.Lock()
        self.job_queue: queue.Queue[str] = queue.Queue()
        self.worker = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker.start()

    def create_job(self, payload: dict[str, Any]) -> Job:
        urls = parse_url_input(payload.get("url_text", ""))
        if not urls:
            raise ValueError("请至少输入一个 Apple Music 链接。")
        url_preview = [classify_url(url) for url in urls]
        if any(not item["valid"] for item in url_preview):
            raise ValueError("包含无法识别的链接，请先修正。")
        if any(not item["supported"] for item in url_preview):
            unsupported = ", ".join(sorted({item["kind"] for item in url_preview if not item["supported"]}))
            raise ValueError(f"首版仅支持歌曲、专辑和歌单，当前包含不支持的类型：{unsupported}")

        settings = self.settings_store.save(payload)
        job = Job(
            id=secrets.token_hex(8),
            urls=urls,
            url_preview=url_preview,
            payload={**settings.__dict__},
            command=build_download_command({**settings.__dict__, "urls": urls}),
        )
        with self.jobs_lock:
            self.jobs[job.id] = job
        self.job_queue.put(job.id)
        return job

    def get_job(self, job_id: str) -> Job | None:
        with self.jobs_lock:
            return self.jobs.get(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.jobs_lock:
            jobs = sorted(self.jobs.values(), key=lambda job: job.created_at, reverse=True)
            return [job.to_dict() for job in jobs]

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if not job or job.status != "queued":
            return False
        job.status = "cancelled"
        job.finished_at = time.time()
        return True

    def _worker_loop(self) -> None:
        while True:
            job_id = self.job_queue.get()
            try:
                job = self.get_job(job_id)
                if not job or job.status == "cancelled":
                    continue
                self._run_job(job)
            finally:
                self.job_queue.task_done()

    def _run_job(self, job: Job) -> None:
        job.status = "running"
        job.started_at = time.time()
        try:
            token = self.auth_manager.get_media_user_token()
            service = DownloadService(
                media_user_token=token,
                log_callback=job.append_log,
                paths=self.paths,
            )
            result = service.run_sync(
                DownloadJob(
                    urls=job.urls,
                    output_path=job.payload["output_path"],
                    overwrite=job.payload["overwrite"],
                    save_cover=job.payload["save_cover"],
                    log_level=job.payload["log_level"],
                    song_codec=job.payload["song_codec"],
                    use_wrapper=job.payload["use_wrapper"],
                    wrapper_decrypt_ip=job.payload["wrapper_decrypt_ip"],
                )
            )
            job.result = result.to_dict()
            job.status = "completed" if result.errors == 0 else "failed"
            job.return_code = 0 if result.errors == 0 else 1
            if result.errors:
                job.error_category = "download"
                job.error_message = "任务包含失败项，请查看日志。"
        except Exception as exc:
            job.append_log(str(exc))
            job.status = "failed"
            job.return_code = 1
            job.error_category = _classify_error(exc)
            job.error_message = str(exc)
        finally:
            job.finished_at = time.time()


class WebGuiHandler(BaseHTTPRequestHandler):
    server_version = "gamdl-web/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_html()
            return
        if parsed.path == "/api/settings":
            settings = self.server.settings_store.load()
            wrapper_status = self.server.wrapper_manager.probe_status(
                settings.wrapper_decrypt_ip
            )
            self._send_json(
                {
                    "settings": settings.__dict__,
                    "wrapper_status": wrapper_status.__dict__,
                    "runtime": self.server.runtime().to_dict(),
                }
            )
            return
        if parsed.path == "/api/jobs":
            self._send_json({"jobs": self.server.job_manager.list_jobs()})
            return
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/open-with-options"):
            self._handle_job_open_with_options(parsed.path)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = self.server.job_manager.get_job(job_id)
            if not job:
                self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
                return
            self._send_json(job.to_dict())
            return
        if parsed.path == "/api/auth/status":
            self._send_json({"session": self.server.auth_manager.get_session_status(verify=False).__dict__})
            return
        if parsed.path == "/api/logs":
            self._send_json({"channels": self.server.log_store.export()})
            return
        if parsed.path == "/api/about":
            settings = self.server.settings_store.load()
            self._send_json(
                {
                    "version": __version__,
                    "original_project_name": "Gamdl (Glomatico's Apple Music Downloader)",
                    "original_project_url": "https://github.com/glomatico/gamdl",
                    "alac_max_spec": "24-bit / 192 kHz",
                    "alac_spec_note": "具体取决于歌曲本身是否提供对应规格。",
                    "supported_browser_imports": SUPPORTED_BROWSER_IMPORTS,
                    "supported_download_kinds": sorted(SUPPORTED_DOWNLOAD_KINDS),
                    "distribution_audio_default": "aac-legacy",
                    "alac_mode": "external-wrapper-only",
                    "runtime": self.server.runtime().to_dict(),
                    "wrapper_status": self.server.wrapper_manager.probe_status(
                        settings.wrapper_decrypt_ip
                    ).__dict__,
                }
            )
            return
        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        payload = self._read_json()

        if parsed.path == "/api/preview":
            urls = parse_url_input(payload.get("url_text", ""))
            preview = [classify_url(url) for url in urls]
            self._send_json(
                {
                    "count": len(preview),
                    "preview": preview,
                    "summary": self._build_preview_summary(preview),
                }
            )
            return

        if parsed.path == "/api/settings":
            try:
                settings = self.server.settings_store.save(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
                return
            self.server.set_log_level(settings.log_level)
            self._send_json(
                {
                    "settings": settings.__dict__,
                    "wrapper_status": self.server.wrapper_manager.probe_status(
                        settings.wrapper_decrypt_ip
                    ).__dict__,
                    "runtime": self.server.runtime().to_dict(),
                }
            )
            return

        if parsed.path == "/api/settings/validate":
            try:
                output_path = self.server.settings_store.validate_output_path(
                    payload.get("output_path", "")
                )
            except ValueError as exc:
                self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"output_path": output_path})
            return

        if parsed.path == "/api/desktop/select-folder":
            if not self.server.folder_picker:
                self._send_json({"error": "当前环境不支持原生文件夹选择器。"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                selected_path = self.server.folder_picker()
            except Exception as exc:
                self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
                return
            if not selected_path:
                self._send_json({"selected": False})
                return
            try:
                output_path = self.server.settings_store.validate_output_path(selected_path)
            except ValueError as exc:
                self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"selected": True, "output_path": output_path})
            return

        if parsed.path == "/api/jobs":
            try:
                job = self.server.job_manager.create_job(payload)
            except ValueError as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.BAD_REQUEST)
                return
            except Exception as exc:
                self._send_json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)
                return
            self._send_json(job.to_dict(), HTTPStatus.CREATED)
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/cancel"):
            job_id = parsed.path.split("/")[-2]
            cancelled = self.server.job_manager.cancel_job(job_id)
            if not cancelled:
                self._send_json(
                    {"error": "Job not found or not cancellable"},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"ok": True})
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/open-output"):
            self._handle_job_file_action(parsed.path, "open-output", payload)
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/open-latest-file"):
            self._handle_job_file_action(parsed.path, "open-latest-file", payload)
            return

        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/reveal-latest-file"):
            self._handle_job_file_action(parsed.path, "reveal-latest-file", payload)
            return

        if parsed.path == "/api/auth/import-browser":
            browser_name = payload.get("browser")
            if browser_name not in SUPPORTED_BROWSER_IMPORTS:
                self._send_json({"error": "Unsupported browser"}, HTTPStatus.BAD_REQUEST)
                return
            try:
                session = self.server.auth_manager.import_from_browser(
                    BrowserType(browser_name),
                    language="zh-CN",
                )
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "category": _classify_error(exc)},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"session": session.__dict__})
            return

        if parsed.path == "/api/auth/login-webview":
            try:
                session = self.server.auth_manager.login_with_webview()
            except Exception as exc:
                self._send_json(
                    {"error": str(exc), "category": _classify_error(exc)},
                    HTTPStatus.BAD_REQUEST,
                )
                return
            self._send_json({"session": session.__dict__})
            return

        if parsed.path == "/api/auth/logout":
            self._send_json({"session": self.server.auth_manager.logout().__dict__})
            return

        if parsed.path == "/api/diagnostics/export":
            bundle = self.server.diagnostics.export_bundle()
            self._send_json({"bundle_path": str(bundle)})
            return

        self._send_json({"error": "Not found"}, HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8"))

    def _send_html(self) -> None:
        body = INDEX_HTML.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _build_preview_summary(preview: list[dict[str, Any]]) -> dict[str, int]:
        summary: dict[str, int] = {}
        for item in preview:
            key = item["kind"]
            summary[key] = summary.get(key, 0) + 1
        return summary

    def _handle_job_file_action(
        self,
        path: str,
        action: str,
        payload: dict[str, Any] | None = None,
    ) -> None:
        job_id = path.split("/")[-2]
        job = self.server.job_manager.get_job(job_id)
        if not job:
            self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
            return

        try:
            if action == "open-output":
                output_path = str(job.payload.get("output_path") or "")
                if not output_path:
                    raise RuntimeError("当前任务没有可用的下载目录。")
                self.server.file_actions.open_output(output_path)
            else:
                latest_media_path = ((job.result or {}).get("latest_media_path") or "").strip()
                if not latest_media_path:
                    raise RuntimeError("当前任务没有可打开的下载文件。")
                if action == "open-latest-file":
                    choose_application = bool((payload or {}).get("choose_application"))
                    application = (
                        str((payload or {}).get("application_path") or (payload or {}).get("application") or "").strip()
                        or None
                    )
                    if choose_application:
                        application = self.server.file_actions.choose_application()
                        if application is None:
                            self._send_json({"cancelled": True})
                            return
                    self.server.file_actions.open_file(latest_media_path, application=application)
                else:
                    self.server.file_actions.reveal_file(latest_media_path)
        except RuntimeError as exc:
            self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json({"ok": True})

    def _handle_job_open_with_options(self, path: str) -> None:
        job_id = path.split("/")[-2]
        job = self.server.job_manager.get_job(job_id)
        if not job:
            self._send_json({"error": "Job not found"}, HTTPStatus.NOT_FOUND)
            return

        latest_media_path = ((job.result or {}).get("latest_media_path") or "").strip()
        if not latest_media_path:
            self._send_json(
                {"error": "当前任务没有可打开的下载文件。", "category": "filesystem"},
                HTTPStatus.BAD_REQUEST,
            )
            return

        settings = self.server.settings_store.load()
        try:
            options = self.server.file_actions.get_open_with_options(
                latest_media_path,
                configured_application=settings.open_file_application,
            )
        except RuntimeError as exc:
            self._send_json({"error": str(exc), "category": "filesystem"}, HTTPStatus.BAD_REQUEST)
            return

        self._send_json(
            {
                "supported": True,
                "latest_media_path": latest_media_path,
                "options": options,
            }
        )


class WebGuiServer(ThreadingHTTPServer):
    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls,
        paths: AppPaths,
        log_store: AppLogStore | None,
        folder_picker: Callable[[], str | None] | None = None,
        file_actions: DesktopFileActions | None = None,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.paths = paths
        self.log_store = log_store or AppLogStore()
        self.settings_store = AppSettingsStore(paths)
        self.auth_manager = AuthManager(paths)
        self.job_manager = JobManager(self.auth_manager, self.settings_store, paths)
        self.diagnostics = DiagnosticsService(paths, self.settings_store, self.log_store)
        self.wrapper_manager = WrapperManager()
        self.folder_picker = folder_picker
        self.file_actions = file_actions or DesktopFileActions()

    def set_log_level(self, level: str) -> None:
        for logger_name in ("gamdl", "gamdl.app.auth", "gamdl.app.download", "gamdl.app.web"):
            logging.getLogger(logger_name).setLevel(level)

    def runtime(self):
        return detect_desktop_runtime(
            folder_picker_supported=self.folder_picker is not None,
            file_actions_supported=self.file_actions.supported,
        )


def create_server(
    host: str,
    port: int,
    *,
    paths: AppPaths | None = None,
    log_store: AppLogStore | None = None,
    folder_picker: Callable[[], str | None] | None = None,
    file_actions: DesktopFileActions | None = None,
) -> WebGuiServer:
    return WebGuiServer(
        (host, port),
        WebGuiHandler,
        paths or AppPaths(),
        log_store or AppLogStore(),
        folder_picker=folder_picker,
        file_actions=file_actions,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Apple Music Downloader local web GUI")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Bind host")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="Bind port")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Don't automatically open the browser",
    )
    args = parser.parse_args()

    paths = AppPaths()
    settings_store = AppSettingsStore(paths)
    log_store = AppLogStore()
    configure_app_logging(paths, log_store, settings_store.load().log_level)
    server = create_server(args.host, args.port, paths=paths, log_store=log_store)
    url = f"http://{args.host}:{server.server_address[1]}"
    print(f"Apple Music Downloader Web GUI listening on {url}")
    if not args.no_open:
        threading.Timer(0.4, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def _classify_error(exc: Exception) -> str:
    message = str(exc).lower()
    if "subscription" in message:
        return "session"
    if "permission" in message or "权限" in message:
        return "filesystem"
    if "login" in message or "token" in message or "登录" in message:
        return "login"
    return "download"


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Apple Music Downloader</title>
  <style>
    :root {
      --bg: #f3f4f6;
      --bg-strong: #eceef2;
      --panel: rgba(255,255,255,.9);
      --panel-2: rgba(250,251,252,.98);
      --line: #e2e5ea;
      --line-strong: #d3d8e0;
      --ink: #15171a;
      --muted: #667085;
      --accent: #0a84ff;
      --accent-soft: #f2f4f7;
      --accent-soft-strong: #e7f0ff;
      --success: #0e7c4f;
      --warn: #b25d11;
      --danger: #a5332c;
      --shadow: 0 18px 48px rgba(15, 23, 42, .08);
      --shadow-soft: 0 4px 18px rgba(15, 23, 42, .04);
      --radius-xl: 22px;
      --radius-lg: 16px;
      --radius-md: 12px;
    }
    * { box-sizing: border-box; }
    html, body { height: 100%; }
    body {
      margin: 0;
      background:
        radial-gradient(circle at top left, rgba(255,255,255,.9), transparent 22%),
        linear-gradient(180deg, #f8f9fb 0%, var(--bg) 100%);
      color: var(--ink);
      font-family: "SF Pro Display", "PingFang SC", -apple-system, BlinkMacSystemFont, sans-serif;
    }
    .app-shell {
      min-height: 100vh;
      padding: 24px;
      display: grid;
      grid-template-columns: 300px minmax(0, 1fr);
      gap: 18px;
    }
    .sidebar, .main-panel {
      background: var(--panel);
      border: 1px solid rgba(255,255,255,.78);
      border-radius: 26px;
      box-shadow: var(--shadow);
      /* WKWebView treats blurred ancestors as containing blocks for fixed menus. */
    }
    .sidebar {
      padding: 16px 12px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }
    .brand {
      padding: 12px 14px 16px;
      border-bottom: 1px solid var(--line);
    }
    .brand-kicker {
      font-size: 12px;
      color: var(--muted);
      letter-spacing: .12em;
      text-transform: uppercase;
    }
    .brand h1 {
      margin: 10px 0 0;
      font-size: 30px;
      letter-spacing: -.04em;
    }
    .brand p {
      margin: 8px 0 0;
      color: var(--muted);
      line-height: 1.55;
      font-size: 14px;
    }
    .nav {
      display: grid;
      gap: 6px;
    }
    .nav-btn {
      border: 1px solid transparent;
      background: transparent;
      color: var(--ink);
      padding: 13px 16px;
      border-radius: 14px;
      font-size: 15px;
      font-weight: 590;
      text-align: left;
      cursor: pointer;
      transition: .18s ease;
    }
    .nav-btn:hover {
      background: var(--accent-soft);
      border-color: var(--line);
    }
    .nav-btn.active {
      background: #0a84ff;
      border-color: #0a84ff;
      color: #fff;
      box-shadow: 0 8px 18px rgba(10, 132, 255, .16);
    }
    .sidebar-foot {
      margin-top: auto;
      padding: 16px 14px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
      line-height: 1.55;
    }
    .main-panel {
      padding: 26px 28px 30px;
      display: flex;
      flex-direction: column;
      gap: 18px;
    }
    .topbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .topbar h2 {
      margin: 0;
      font-size: 34px;
      letter-spacing: -.04em;
    }
    .status-chip {
      display: inline-flex;
      align-items: center;
      gap: 8px;
      padding: 10px 14px;
      border-radius: 999px;
      background: rgba(255,255,255,.9);
      border: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }
    .dot {
      width: 8px;
      height: 8px;
      border-radius: 999px;
      background: #c3c8d0;
    }
    .dot.success { background: var(--success); }
    .dot.warn { background: var(--warn); }
    .page {
      display: none;
      gap: 18px;
    }
    .page.active {
      display: grid;
    }
    .card {
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: var(--radius-xl);
      overflow: hidden;
      box-shadow: var(--shadow-soft);
    }
    .card-head {
      padding: 18px 22px 14px;
      border-bottom: 1px solid var(--line);
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 14px;
    }
    .card-head h3 {
      margin: 0;
      font-size: 20px;
      letter-spacing: -.03em;
    }
    .card-body {
      padding: 18px 22px 22px;
    }
    .lead {
      color: var(--muted);
      line-height: 1.6;
      font-size: 14px;
      margin: 0;
    }
    .grid {
      display: grid;
      gap: 16px;
    }
    .grid.two {
      grid-template-columns: 1.25fr .95fr;
    }
    textarea, input, select {
      width: 100%;
      border: 1px solid var(--line-strong);
      border-radius: 14px;
      padding: 14px 16px;
      background: #fff;
      color: var(--ink);
      font: inherit;
      box-shadow: inset 0 1px 0 rgba(255,255,255,.78);
    }
    textarea:focus, input:focus, select:focus {
      outline: none;
      border-color: rgba(10,132,255,.55);
      box-shadow: 0 0 0 4px rgba(10,132,255,.12);
    }
    select {
      appearance: none;
      -webkit-appearance: none;
      -moz-appearance: none;
      background-color: #f3f4f6;
      background-image: url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='8' viewBox='0 0 12 8' fill='none'%3E%3Cpath d='M1 1.5L6 6.5L11 1.5' stroke='%23667085' stroke-width='1.75' stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 16px center;
      background-size: 12px 8px;
      border-color: #e4e7ec;
      box-shadow: none;
      padding-right: 42px;
    }
    select:hover {
      background-color: #eef1f4;
      border-color: #d9dee6;
    }
    textarea {
      min-height: 190px;
      resize: vertical;
      line-height: 1.55;
    }
    .field {
      display: grid;
      gap: 8px;
    }
    .field label {
      font-size: 13px;
      color: var(--muted);
    }
    .inline {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: wrap;
    }
    .path-picker {
      display: flex;
      align-items: center;
      gap: 12px;
      flex-wrap: nowrap;
    }
    .path-picker input {
      flex: 1 1 auto;
    }
    .path-picker .btn {
      flex: 0 0 auto;
      white-space: nowrap;
    }
    .btn {
      border: 1px solid var(--line-strong);
      background: #fff;
      color: var(--ink);
      display: inline-flex;
      align-items: center;
      justify-content: center;
      padding: 12px 16px;
      border-radius: 16px;
      font: inherit;
      cursor: pointer;
      text-decoration: none;
      transition: .18s ease;
    }
    .btn:hover {
      transform: translateY(-1px);
      border-color: #c8ced8;
      background: #fbfcfd;
    }
    .btn.primary {
      background: var(--accent);
      color: #fff;
      border-color: var(--accent);
      box-shadow: 0 10px 18px rgba(10, 132, 255, .18);
    }
    .btn.soft {
      background: var(--accent-soft);
      border-color: var(--line);
    }
    .btn.danger { color: var(--danger); }
    .btn[disabled] {
      cursor: not-allowed;
      opacity: .45;
      transform: none;
    }
    .menu-anchor {
      position: relative;
      display: inline-block;
    }
    .split-pill-anchor {
      position: relative;
      display: inline-flex;
      align-items: stretch;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: var(--accent-soft);
      overflow: visible;
      transition: background-color .18s ease, border-color .18s ease, box-shadow .18s ease;
    }
    .split-pill-anchor:hover {
      border-color: #c8ced8;
      background: #fbfcfd;
    }
    .split-pill-anchor.open {
      border-color: rgba(10, 132, 255, .34);
      box-shadow: 0 10px 18px rgba(10, 132, 255, .12);
    }
    .split-pill-anchor.loading {
      border-color: rgba(10, 132, 255, .28);
      box-shadow: 0 8px 14px rgba(10, 132, 255, .08);
      cursor: progress;
    }
    .split-pill-anchor.disabled {
      opacity: .45;
    }
    .split-pill-anchor.disabled:hover {
      border-color: var(--line);
      background: var(--accent-soft);
      box-shadow: none;
    }
    .split-pill-main,
    .split-pill-toggle {
      border: 0;
      background: transparent;
      color: var(--ink);
      font: inherit;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 48px;
      padding: 12px 16px;
      transition: background .18s ease, color .18s ease;
    }
    .split-pill-main {
      min-width: 108px;
      white-space: nowrap;
      border-radius: 16px 0 0 16px;
    }
    .split-pill-toggle {
      width: 46px;
      padding: 0;
      position: relative;
      border-radius: 0 16px 16px 0;
      flex: 0 0 46px;
    }
    .split-pill-toggle::before {
      content: "";
      position: absolute;
      left: 0;
      top: 10px;
      bottom: 10px;
      width: 1px;
      background: rgba(195, 201, 211, .95);
    }
    .split-pill-main:hover,
    .split-pill-toggle:hover {
      background: rgba(10, 132, 255, .08);
    }
    .split-pill-anchor.open .split-pill-toggle {
      background: rgba(10, 132, 255, .12);
    }
    .split-pill-anchor.loading .split-pill-toggle {
      background: rgba(10, 132, 255, .16);
    }
    .split-pill-main:focus-visible,
    .split-pill-toggle:focus-visible,
    .finder-menu-item:focus-visible {
      outline: 2px solid rgba(10, 132, 255, .35);
      outline-offset: -2px;
    }
    .split-pill-main[disabled],
    .split-pill-toggle[disabled] {
      cursor: not-allowed;
    }
    .split-pill-chevron {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      font-size: 17px;
      line-height: 1;
      transform: translateX(1px);
    }
    .finder-menu {
      position: fixed;
      top: 0;
      left: 0;
      min-width: 0;
      max-width: min(360px, calc(100vw - 48px));
      max-height: min(360px, calc(100vh - 140px));
      overflow-y: auto;
      overscroll-behavior: contain;
      scrollbar-gutter: stable;
      padding: 8px;
      border: 1px solid rgba(195, 201, 211, .95);
      border-radius: 16px;
      background: rgba(255,255,255,.96);
      box-shadow:
        0 24px 52px rgba(15, 23, 42, .18),
        0 4px 14px rgba(15, 23, 42, .08);
      z-index: 200;
    }
    .finder-menu.measuring {
      visibility: hidden;
      pointer-events: none;
    }
    .finder-menu[hidden] {
      display: none;
    }
    .finder-menu-item {
      width: 100%;
      border: 0;
      background: transparent;
      text-align: left;
      border-radius: 12px;
      padding: 11px 13px;
      font: inherit;
      color: var(--ink);
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .finder-menu-item:hover {
      background: rgba(10, 132, 255, .12);
    }
    .finder-menu-item[disabled] {
      cursor: not-allowed;
      opacity: .45;
      background: transparent;
    }
    .finder-menu-separator {
      height: 1px;
      margin: 6px 2px;
      background: rgba(210, 216, 225, .95);
    }
    .finder-menu-meta {
      color: var(--muted);
      font-size: 12px;
      white-space: nowrap;
    }
    .finder-menu-label {
      flex: 1 1 auto;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .stats {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
    }
    .stat {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px;
    }
    .stat .label {
      font-size: 12px;
      color: var(--muted);
      text-transform: uppercase;
      letter-spacing: .08em;
    }
    .stat .value {
      margin-top: 10px;
      font-size: 28px;
      letter-spacing: -.04em;
    }
    .list {
      display: grid;
      gap: 10px;
    }
    .list-item {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
      display: grid;
      gap: 8px;
    }
    .list-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .muted { color: var(--muted); }
    .badge {
      display: inline-flex;
      align-items: center;
      border-radius: 999px;
      padding: 7px 11px;
      font-size: 12px;
      background: #f5f7fa;
      border: 1px solid #e7ebf0;
      color: var(--muted);
    }
    .badge.success { color: var(--success); }
    .badge.warn { color: var(--warn); }
    .badge.danger { color: var(--danger); }
    pre {
      margin: 0;
      white-space: pre-wrap;
      word-break: break-word;
      font-family: "SF Mono", "JetBrains Mono", monospace;
      font-size: 12px;
      line-height: 1.55;
      color: #27231f;
    }
    .segment {
      display: inline-flex;
      padding: 4px;
      border-radius: 999px;
      background: #f2f4f7;
      border: 1px solid var(--line);
    }
    .segment button {
      border: 0;
      background: transparent;
      color: var(--muted);
      padding: 9px 12px;
      border-radius: 999px;
      cursor: pointer;
      font: inherit;
    }
    .segment button.active {
      background: #fff;
      color: var(--ink);
      box-shadow: 0 4px 10px rgba(15, 23, 42, .08);
    }
    .preview-table {
      display: grid;
      gap: 8px;
    }
    .preview-row {
      display: grid;
      grid-template-columns: 110px 110px 1fr;
      gap: 12px;
      align-items: center;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 12px 14px;
    }
    .toast {
      position: fixed;
      right: 26px;
      bottom: 26px;
      background: rgba(21, 21, 21, .92);
      color: #fff;
      padding: 14px 16px;
      border-radius: 16px;
      min-width: 240px;
      box-shadow: var(--shadow);
      opacity: 0;
      pointer-events: none;
      transform: translateY(10px);
      transition: .22s ease;
    }
    .toast.show {
      opacity: 1;
      pointer-events: auto;
      transform: translateY(0);
    }
    .setup-overlay {
      position: fixed;
      inset: 0;
      padding: 32px;
      background: rgba(243, 245, 248, .72);
      backdrop-filter: blur(24px);
      z-index: 30;
      display: none;
      align-items: center;
      justify-content: center;
    }
    .setup-overlay.show {
      display: flex;
    }
    .setup-shell {
      width: min(1120px, 100%);
      display: grid;
      grid-template-columns: 0.92fr 1.08fr;
      gap: 18px;
    }
    .setup-hero,
    .setup-panel {
      background: rgba(255,255,255,.92);
      border: 1px solid rgba(255,255,255,.78);
      border-radius: 30px;
      box-shadow: var(--shadow);
      padding: 28px;
    }
    .setup-hero {
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      min-height: 640px;
      background:
        radial-gradient(circle at top left, rgba(255,255,255,.96), transparent 36%),
        linear-gradient(180deg, rgba(251,252,253,.98) 0%, rgba(243,245,248,.96) 100%);
    }
    .setup-kicker {
      font-size: 12px;
      color: var(--muted);
      letter-spacing: .14em;
      text-transform: uppercase;
    }
    .setup-hero h1 {
      margin: 14px 0 10px;
      font-size: 52px;
      line-height: .96;
      letter-spacing: -.06em;
    }
    .setup-hero p {
      margin: 0;
      color: var(--muted);
      line-height: 1.7;
      font-size: 15px;
      max-width: 34ch;
    }
    .setup-points {
      display: grid;
      gap: 12px;
      margin-top: 26px;
    }
    .setup-point {
      background: rgba(255,255,255,.8);
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 14px 16px;
    }
    .setup-point strong {
      display: block;
      font-size: 15px;
      margin-bottom: 4px;
    }
    .setup-meta {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding-top: 20px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 13px;
    }
    .setup-steps {
      display: grid;
      gap: 18px;
    }
    .setup-step {
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 18px;
      padding: 20px;
      display: grid;
      gap: 14px;
    }
    .setup-step-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
    }
    .setup-step-index {
      width: 34px;
      height: 34px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      border-radius: 999px;
      background: var(--accent);
      color: #fff;
      font-size: 14px;
      box-shadow: 0 8px 18px rgba(10, 132, 255, .18);
    }
    .setup-actions {
      display: grid;
      gap: 12px;
      margin-top: 12px;
    }
    .setup-status {
      min-height: 22px;
      color: var(--muted);
      font-size: 13px;
    }
    .setup-complete {
      display: grid;
      gap: 10px;
      padding-top: 6px;
    }
    .setup-complete .btn[disabled] {
      cursor: not-allowed;
      opacity: .45;
      transform: none;
    }
    .guide-steps {
      display: grid;
      gap: 12px;
    }
    .guide-step {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 16px;
      padding: 16px 18px;
      display: grid;
      gap: 10px;
    }
    .guide-step strong {
      font-size: 16px;
      letter-spacing: -.02em;
    }
    .guide-links {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    @media (max-width: 1180px) {
      .app-shell { grid-template-columns: 1fr; }
      .grid.two { grid-template-columns: 1fr; }
      .stats { grid-template-columns: repeat(2, minmax(0, 1fr)); }
      .setup-shell { grid-template-columns: 1fr; }
      .setup-hero { min-height: auto; }
    }
  </style>
</head>
<body>
  <div class="setup-overlay" id="setup-overlay">
    <div class="setup-shell">
      <section class="setup-hero">
        <div>
          <div class="setup-kicker">Welcome / Setup</div>
          <h1>先把入口配置好，再开始下载。</h1>
          <p>首次使用前，请先选择下载目录，并登录你的 Apple Music 账号。完成后就可以直接开始下载。</p>
          <div class="setup-points">
            <div class="setup-point">
              <strong>下载目录</strong>
              下载的歌曲、专辑和歌单都会保存到这里。你之后也可以在设置里修改。
            </div>
            <div class="setup-point">
              <strong>账号登录</strong>
              使用你自己的 Apple Music 账号登录。你可以选择内置登录，也可以导入浏览器登录状态。
            </div>
            <div class="setup-point">
              <strong>完成设置后开始下载</strong>
              首次设置完成后，后续启动会直接进入主界面。
            </div>
          </div>
        </div>
        <div class="setup-meta">
          <span>Apple Music Downloader</span>
          <span id="setup-footer-status">等待完成首次配置</span>
        </div>
      </section>
      <section class="setup-panel">
        <div class="setup-steps">
          <div class="setup-step">
            <div class="setup-step-head">
              <div class="inline">
                <span class="setup-step-index">1</span>
                <div>
                  <strong>选择下载目录</strong>
                  <div class="muted">建议保留在 `~/Downloads/Apple Music Downloader`，也可以改成你想要的目标位置。</div>
                  <div class="muted">默认推荐使用 AAC；如果你已经配置好外部 wrapper，也可以选择 ALAC 或杜比全景声。</div>
                </div>
              </div>
              <span class="badge" id="setup-path-badge">待确认</span>
            </div>
            <div class="field">
              <label for="setup-output-path">下载目录</label>
              <div class="path-picker">
                <input id="setup-output-path" type="text" readonly />
                <button class="btn" id="setup-select-output-btn" type="button">选择文件夹</button>
              </div>
              <div class="muted" id="setup-output-path-copy">使用“选择文件夹”即可写入下载目录。</div>
            </div>
          </div>
          <div class="setup-step">
            <div class="setup-step-head">
              <div class="inline">
                <span class="setup-step-index">2</span>
                <div>
                  <strong>完成 Apple Music 登录</strong>
                  <div class="muted" id="setup-login-copy">内置登录是主路径。浏览器导入适合你已经在本机浏览器里登录过 Apple Music 的情况。</div>
                </div>
              </div>
              <span class="badge" id="setup-login-badge">待登录</span>
            </div>
            <div class="inline">
              <button class="btn primary" id="setup-login-webview-btn">内置登录</button>
              <select id="setup-browser-select" style="max-width: 180px">
                <option value="chrome">Chrome</option>
                <option value="edge">Edge</option>
                <option value="brave">Brave</option>
                <option value="firefox">Firefox</option>
              </select>
              <button class="btn" id="setup-browser-import-btn">导入浏览器登录态</button>
            </div>
            <div class="setup-status" id="setup-login-status">未检测到可用会话。</div>
          </div>
          <div class="setup-complete">
            <button class="btn primary" id="complete-setup-btn" disabled>完成首次设置</button>
            <div class="setup-status" id="setup-complete-status">完成登录并确认下载目录后，才能进入主界面。</div>
          </div>
        </div>
      </section>
    </div>
  </div>

  <div class="app-shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-kicker">Apple Music Downloader</div>
        <h1>Apple Music Downloader</h1>
        <p>下载歌曲、专辑和歌单。默认支持 AAC；如果已经配置外部 wrapper，也可以使用 ALAC 和杜比全景声。</p>
      </div>
      <nav class="nav">
        <button class="nav-btn active" data-page="download">下载</button>
        <button class="nav-btn" data-page="account">账号</button>
        <button class="nav-btn" data-page="jobs">任务</button>
        <button class="nav-btn" data-page="logs">日志</button>
        <button class="nav-btn" data-page="settings">设置</button>
        <button class="nav-btn" data-page="about">关于</button>
      </nav>
      <div class="sidebar-foot">
        登录状态、日志和诊断信息只保存在当前这台设备上。
      </div>
    </aside>
    <main class="main-panel">
      <div class="topbar">
        <h2 id="page-title">下载</h2>
        <div class="status-chip"><span class="dot" id="session-dot"></span><span id="session-summary">正在读取登录状态</span></div>
      </div>

      <section class="page active" id="page-download">
        <div class="grid two">
          <article class="card">
            <div class="card-head">
              <h3>新建下载任务</h3>
              <button class="btn soft" id="preview-btn">预览链接</button>
            </div>
            <div class="card-body grid">
              <p class="lead">支持歌曲、专辑和歌单。AAC 可直接使用；ALAC 和杜比全景声需要你先配置外部 wrapper。</p>
              <div class="field">
                <label for="url-input">Apple Music 链接</label>
                <textarea id="url-input" placeholder="每行一个链接，或者直接粘贴多个链接"></textarea>
              </div>
              <div class="inline">
                <button class="btn primary" id="submit-job-btn">加入下载队列</button>
                <span class="muted" id="preview-summary">尚未预览</span>
              </div>
            </div>
          </article>
          <article class="card">
            <div class="card-head">
              <h3>当前账号</h3>
            </div>
            <div class="card-body grid">
              <div class="list-item">
                <div class="list-head">
                  <strong id="account-headline">未登录</strong>
                  <span class="badge" id="account-method">No Session</span>
                </div>
                <div class="muted" id="account-detail">登录后会在这里显示 storefront、订阅和限制信息。</div>
              </div>
              <div class="inline">
                <button class="btn primary" id="login-webview-btn">内置登录</button>
                <select id="browser-select" style="max-width: 180px">
                  <option value="chrome">Chrome</option>
                  <option value="edge">Edge</option>
                  <option value="brave">Brave</option>
                  <option value="firefox">Firefox</option>
                </select>
                <button class="btn" id="browser-import-btn">导入浏览器登录态</button>
              </div>
            </div>
          </article>
        </div>

        <article class="card">
          <div class="card-head">
            <h3>链接预览</h3>
          </div>
          <div class="card-body">
            <div class="preview-table" id="preview-list">
              <div class="muted">预览后会显示链接类型和是否在桌面版支持范围内。</div>
            </div>
          </div>
        </article>
      </section>

      <section class="page" id="page-account">
        <article class="card">
          <div class="card-head">
            <h3>账号与登录态</h3>
            <button class="btn danger" id="logout-btn">退出登录</button>
          </div>
          <div class="card-body grid">
            <p class="lead" id="account-login-copy">内置登录适合作为主路径；浏览器导入适合作为兜底。两种方式都只读取你自己的 Apple Music 会话，登录态保存在本机私有目录和 Keychain。</p>
            <div class="list" id="account-status-list"></div>
          </div>
        </article>
      </section>

      <section class="page" id="page-jobs">
        <div class="stats">
          <div class="stat"><div class="label">Queued</div><div class="value" id="stat-queued">0</div></div>
          <div class="stat"><div class="label">Running</div><div class="value" id="stat-running">0</div></div>
          <div class="stat"><div class="label">Completed</div><div class="value" id="stat-completed">0</div></div>
          <div class="stat"><div class="label">Failed</div><div class="value" id="stat-failed">0</div></div>
        </div>
        <article class="card">
          <div class="card-head">
            <h3>任务队列</h3>
            <button class="btn soft" id="refresh-jobs-btn">刷新</button>
          </div>
          <div class="card-body">
            <div class="list" id="jobs-list"><div class="muted">还没有任务。</div></div>
          </div>
        </article>
      </section>

      <section class="page" id="page-logs">
        <article class="card">
          <div class="card-head">
            <h3>应用日志</h3>
            <div class="segment">
              <button class="active" data-log-channel="app">App</button>
              <button data-log-channel="auth">Auth</button>
              <button data-log-channel="download">Download</button>
            </div>
          </div>
          <div class="card-body">
            <div class="list-item"><pre id="logs-output">正在加载日志…</pre></div>
          </div>
        </article>
      </section>

      <section class="page" id="page-settings">
        <article class="card">
          <div class="card-head">
            <h3>桌面版设置</h3>
            <button class="btn primary" id="save-settings-btn">保存设置</button>
          </div>
          <div class="card-body grid">
            <div class="field">
              <label for="output-path">下载目录</label>
              <div class="path-picker">
                <input id="output-path" type="text" readonly />
                <button class="btn" id="select-output-btn" type="button">选择文件夹</button>
              </div>
              <div class="muted" id="output-path-copy">使用“选择文件夹”即可写入下载目录。</div>
            </div>
            <div class="grid two">
              <div class="field">
                <label for="log-level">日志级别</label>
                <select id="log-level">
                  <option value="DEBUG">DEBUG</option>
                  <option value="INFO">INFO</option>
                  <option value="WARNING">WARNING</option>
                  <option value="ERROR">ERROR</option>
                </select>
              </div>
              <div class="field">
                <label for="save-cover">封面文件</label>
                <select id="save-cover">
                  <option value="true">保存</option>
                  <option value="false">不保存</option>
                </select>
              </div>
            </div>
            <div class="grid two">
              <div class="field">
                <label for="song-codec">音质</label>
                <select id="song-codec">
                  <option value="alac">ALAC（需要外部 wrapper）</option>
                  <option value="aac-legacy">AAC</option>
                  <option value="atmos">杜比全景声（需要外部 wrapper）</option>
                </select>
              </div>
              <div class="field">
                <label for="overwrite">覆盖已存在文件</label>
                <select id="overwrite">
                  <option value="false">关闭</option>
                  <option value="true">开启</option>
                </select>
              </div>
            </div>
            <div class="grid two">
              <div class="field">
                <label for="use-wrapper">启用 wrapper</label>
                <select id="use-wrapper">
                  <option value="true">开启</option>
                  <option value="false">关闭</option>
                </select>
              </div>
              <div class="field">
                <label for="browser-import-enabled">浏览器导入</label>
                <select id="browser-import-enabled">
                  <option value="true">允许</option>
                  <option value="false">关闭</option>
                </select>
              </div>
            </div>
            <div class="field">
              <label for="wrapper-decrypt-ip">Wrapper 解密地址</label>
              <input id="wrapper-decrypt-ip" type="text" />
            </div>
            <div class="field" id="open-file-application-field">
              <label for="open-file-application">打开文件应用（可选）</label>
              <input id="open-file-application" type="text" placeholder="Windows 可填 exe 完整路径，例如 C:\\Program Files\\VLC\\vlc.exe" />
              <div class="muted">仅 Windows 简化模式会用到这个值，作为“打开方式”菜单里的自定义应用兜底。</div>
            </div>
            <p class="lead" id="wrapper-status-copy">正式分发版默认使用 AAC。只有当外部 wrapper 已运行或可启动时，才建议切换到 ALAC 或杜比全景声。</p>
          </div>
        </article>
      </section>

      <section class="page" id="page-about">
        <article class="card">
          <div class="card-head">
            <h3>关于应用</h3>
            <button class="btn" id="export-diagnostics-btn">导出诊断包</button>
          </div>
          <div class="card-body grid">
            <p class="lead">Apple Music Downloader 用于下载歌曲、专辑和歌单。默认支持 AAC；如果已经配置外部 wrapper，也可以使用 ALAC 和杜比全景声。</p>
            <div class="list" id="about-list"></div>
          </div>
        </article>
        <article class="card">
          <div class="card-head">
            <h3>ALAC / Wrapper 安装指南</h3>
            <div class="guide-links">
              <a class="btn soft" href="https://docs.docker.com/desktop/setup/install/mac-install/" target="_blank" rel="noreferrer">Docker 官方安装文档</a>
              <a class="btn soft" href="https://github.com/WorldObservationLog/wrapper" target="_blank" rel="noreferrer">Wrapper 源码仓库</a>
            </div>
          </div>
          <div class="card-body">
            <div class="guide-steps">
              <div class="guide-step">
                <strong>1. 先安装 Docker Desktop</strong>
                <div class="muted">在 Mac 上先装好 Docker Desktop，再继续 ALAC。Apple Silicon 和 Intel 要下载不同安装包；官方文档还建议预留至少 4 GB RAM，Apple Silicon 机器建议装 Rosetta 2。</div>
                <pre>可选：Apple Silicon 机器执行
softwareupdate --install-rosetta</pre>
              </div>
              <div class="guide-step">
                <strong>2. 启动 Docker 并确认可用</strong>
                <div class="muted">把 Docker.app 拖进 Applications 后启动，等菜单栏鲸鱼图标显示运行正常，再执行下面命令。</div>
                <pre>docker version</pre>
              </div>
              <div class="guide-step">
                <strong>3. 拉源码并构建 wrapper 镜像</strong>
                <div class="muted">这一步会在本地生成一个可以复用的 wrapper 镜像。</div>
                <pre>git clone https://github.com/WorldObservationLog/wrapper.git
cd wrapper
docker build -t wrapper-local .</pre>
              </div>
              <div class="guide-step">
                <strong>4. 首次登录 wrapper</strong>
                <div class="muted">把下面命令里的 <code>your_apple_id@example.com:your_password</code> 改成你自己的 Apple Music 账号。出现 2FA 时按终端提示完成，看到 <code>response type 6</code> 再结束。</div>
                <pre>docker run --rm -it \
  -v "$PWD/rootfs/data:/app/rootfs/data" \
  -e args="-L your_apple_id@example.com:your_password -F -H 0.0.0.0 -D 10022 -M 20022 -A 30022" \
  wrapper-local</pre>
              </div>
              <div class="guide-step">
                <strong>5. 创建长期运行的 wrapper 容器</strong>
                <div class="muted">容器名请保持 <code>wrapper-latest-10022</code>。这样 App 才能自动识别，并在 Docker 已启动时帮你自动拉起。</div>
                <pre>docker run -d \
  --name wrapper-latest-10022 \
  -v "$PWD/rootfs/data:/app/rootfs/data" \
  -p 10022:10022 \
  -p 20022:20022 \
  -p 30022:30022 \
  -e args="-H 0.0.0.0 -D 10022 -M 20022 -A 30022" \
  wrapper-local</pre>
              </div>
              <div class="guide-step">
                <strong>6. 自检端口，然后回到 App 切 ALAC</strong>
                <div class="muted">如果 10022 和 20022 都能连通，App 里把音质切到 ALAC，并保持解密地址为 <code>127.0.0.1:10022</code>。</div>
                <pre>docker ps --filter name=wrapper-latest-10022
nc -vz 127.0.0.1 10022
nc -vz 127.0.0.1 20022</pre>
              </div>
              <div class="guide-step">
                <strong>7. 以后怎么启动</strong>
                <div class="muted">以后只要 Docker Desktop 已经打开，App 会优先检测现有端口，也会尝试启动这个容器。手动命令只需要这两个：</div>
                <pre>docker start wrapper-latest-10022
docker stop wrapper-latest-10022</pre>
              </div>
            </div>
          </div>
        </article>
      </section>
    </main>
  </div>

  <div class="toast" id="toast"></div>

  <script>
    const state = {
      jobs: [],
      openWithMenuJobId: null,
      openWithMenuRequestId: 0,
      openWithOptionsCache: {},
      session: null,
      settings: null,
      runtime: null,
      wrapperStatus: null,
      logs: {},
      currentLogChannel: 'app',
      needsSetup: true,
      pages: {
        download: '下载',
        account: '账号',
        jobs: '任务',
        logs: '日志',
        settings: '设置',
        about: '关于',
      },
    };

    async function api(path, options = {}) {
      const response = await fetch(path, {
        headers: { 'Content-Type': 'application/json' },
        ...options,
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error || '请求失败');
      }
      return data;
    }

    function showToast(message) {
      const toast = document.getElementById('toast');
      toast.textContent = message;
      toast.classList.add('show');
      window.clearTimeout(showToast.timer);
      showToast.timer = window.setTimeout(() => toast.classList.remove('show'), 2400);
    }

    function escapeHtml(value) {
      return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#39;');
    }

    function getJobById(jobId) {
      return state.jobs.find((job) => job.id === jobId) || null;
    }

    function getLatestMediaPath(jobId) {
      const job = getJobById(jobId);
      return job?.result?.latest_media_path || '';
    }

    function getCachedOpenWithOptions(jobId) {
      const latestMediaPath = getLatestMediaPath(jobId);
      const cached = state.openWithOptionsCache[jobId];
      if (!cached) return null;
      if (!latestMediaPath || cached.latestMediaPath !== latestMediaPath) {
        delete state.openWithOptionsCache[jobId];
        return null;
      }
      return cached.data;
    }

    function cacheOpenWithOptions(jobId, latestMediaPath, data) {
      if (!latestMediaPath) return;
      state.openWithOptionsCache[jobId] = {
        latestMediaPath,
        data,
      };
    }

    function setOpenWithMenuLoading(jobId, loading) {
      const anchor = document.querySelector(`.split-pill-anchor[data-job-id="${jobId}"]`);
      if (!anchor) return;
      anchor.classList.toggle('loading', loading);
    }

    function activatePage(page) {
      closeOpenWithMenus();
      document.querySelectorAll('.nav-btn').forEach((button) => {
        button.classList.toggle('active', button.dataset.page === page);
      });
      document.querySelectorAll('.page').forEach((node) => node.classList.remove('active'));
      document.getElementById(`page-${page}`).classList.add('active');
      document.getElementById('page-title').textContent = state.pages[page];
    }

    function syncSetupOverlay() {
      const overlay = document.getElementById('setup-overlay');
      const completeButton = document.getElementById('complete-setup-btn');
      const pathBadge = document.getElementById('setup-path-badge');
      const loginBadge = document.getElementById('setup-login-badge');
      const footerStatus = document.getElementById('setup-footer-status');
      const completeStatus = document.getElementById('setup-complete-status');
      const pathValue = document.getElementById('setup-output-path').value.trim();
      const hasPath = Boolean(pathValue);
      const hasSession = Boolean(state.session && state.session.connected);

      if (hasPath) {
        pathBadge.textContent = '已确认';
        pathBadge.className = 'badge success';
      } else {
        pathBadge.textContent = '待确认';
        pathBadge.className = 'badge';
      }

      if (hasSession) {
        loginBadge.textContent = '已登录';
        loginBadge.className = 'badge success';
      } else {
        loginBadge.textContent = '待登录';
        loginBadge.className = 'badge';
      }

      state.needsSetup = !(state.settings && state.settings.setup_completed);
      overlay.classList.toggle('show', state.needsSetup);
      completeButton.disabled = !(hasPath && hasSession);

      if (!state.needsSetup) {
        footerStatus.textContent = '首次配置已完成';
        completeStatus.textContent = '你已经完成首次设置，后续可在设置页修改下载目录。';
        return;
      }

      if (hasPath && hasSession) {
        footerStatus.textContent = '可以完成首次设置';
        completeStatus.textContent = '现在可以进入主界面了。';
      } else if (!hasPath && !hasSession) {
        footerStatus.textContent = '等待目录和登录完成';
        completeStatus.textContent = '先确认下载目录，再完成登录。';
      } else if (!hasPath) {
        footerStatus.textContent = '等待目录确认';
        completeStatus.textContent = '还需要确认下载目录。';
      } else {
        footerStatus.textContent = '等待登录完成';
        completeStatus.textContent = '还需要完成 Apple Music 登录。';
      }
    }

    function badgeForStatus(status) {
      const statusMap = {
        queued: ['Queued', 'badge'],
        running: ['Running', 'badge warn'],
        completed: ['Completed', 'badge success'],
        failed: ['Failed', 'badge danger'],
        cancelled: ['Cancelled', 'badge'],
      };
      return statusMap[status] || [status, 'badge'];
    }

    function renderSession(session) {
      state.session = session;
      const dot = document.getElementById('session-dot');
      const summary = document.getElementById('session-summary');
      const headline = document.getElementById('account-headline');
      const method = document.getElementById('account-method');
      const detail = document.getElementById('account-detail');
      const accountList = document.getElementById('account-status-list');

      if (!session || !session.connected) {
        dot.className = 'dot';
        summary.textContent = session?.last_error || '未登录';
        headline.textContent = '未登录';
        method.textContent = 'No Session';
        method.className = 'badge';
        detail.textContent = state.runtime?.native_login_supported
          ? '请先完成内置登录或浏览器导入。'
          : '请先在本机浏览器登录 Apple Music，再导入浏览器登录态。';
        accountList.innerHTML = '<div class="list-item"><div class="muted">当前没有可用会话。</div></div>';
        document.getElementById('setup-login-status').textContent = session?.last_error || '未检测到可用会话。';
        syncSetupOverlay();
        return;
      }

      dot.className = session.active_subscription ? 'dot success' : 'dot warn';
      summary.textContent = `已连接 ${session.storefront || 'unknown'} · ${session.login_method || 'saved-session'}`;
      headline.textContent = `Storefront: ${session.storefront || 'unknown'}`;
      method.textContent = session.login_method || 'saved-session';
      method.className = 'badge success';
      detail.textContent = session.active_subscription
        ? '账号订阅有效，可以开始下载。'
        : '账号已登录，但未检测到可用订阅。';

      const items = [
        ['登录方式', session.login_method || 'unknown'],
        ['浏览器来源', session.browser || (state.runtime?.native_login_supported ? '内置登录' : '浏览器导入')],
        ['Storefront', session.storefront || 'unknown'],
        ['语言', session.language || 'zh-CN'],
        ['订阅状态', session.active_subscription ? '有效' : '无效'],
      ];
      if (session.account_restrictions) {
        items.push(['内容限制', JSON.stringify(session.account_restrictions)]);
      }
      accountList.innerHTML = items.map(([label, value]) => `
        <div class="list-item">
          <div class="list-head"><strong>${label}</strong><span class="muted">${value}</span></div>
        </div>
      `).join('');
      document.getElementById('setup-login-status').textContent = session.active_subscription
        ? `已登录 ${session.storefront || 'unknown'}，订阅状态有效。`
        : '已登录，但未检测到可用订阅。';
      syncSetupOverlay();
    }

    function renderPreview(data) {
      const summary = document.getElementById('preview-summary');
      summary.textContent = `${data.count} 个链接，${Object.entries(data.summary).map(([kind, count]) => `${kind} ${count}`).join(' / ') || '无'}`;
      const container = document.getElementById('preview-list');
      if (!data.preview.length) {
        container.innerHTML = '<div class="muted">还没有可预览的链接。</div>';
        return;
      }
      container.innerHTML = data.preview.map((item) => `
        <div class="preview-row">
          <span class="${item.supported ? 'badge success' : 'badge danger'}">${item.supported ? 'Supported' : 'Unsupported'}</span>
          <strong>${item.label}</strong>
          <span class="muted">${item.url}</span>
        </div>
      `).join('');
    }

    function renderJobs(jobs) {
      state.jobs = jobs;
      const activeJobIds = new Set(jobs.map((job) => job.id));
      Object.keys(state.openWithOptionsCache).forEach((jobId) => {
        if (!activeJobIds.has(jobId)) {
          delete state.openWithOptionsCache[jobId];
        }
      });
      const counts = { queued: 0, running: 0, completed: 0, failed: 0 };
      jobs.forEach((job) => {
        if (counts[job.status] !== undefined) counts[job.status] += 1;
      });
      document.getElementById('stat-queued').textContent = counts.queued;
      document.getElementById('stat-running').textContent = counts.running;
      document.getElementById('stat-completed').textContent = counts.completed;
      document.getElementById('stat-failed').textContent = counts.failed;

      const list = document.getElementById('jobs-list');
      if (!jobs.length) {
        list.innerHTML = '<div class="muted">还没有任务。</div>';
        return;
      }

      list.innerHTML = jobs.map((job) => {
        const [statusLabel, statusClass] = badgeForStatus(job.status);
        const result = job.result || {};
        const latestMediaPath = result.latest_media_path || '';
        const supportsFileActions = Boolean(state.runtime?.file_actions_supported);
        const canShowFileActions = supportsFileActions && (job.status === 'completed' || latestMediaPath);
        const fileActionHint = latestMediaPath
          ? `<div class="muted">最近成功文件：${escapeHtml(latestMediaPath)}</div>`
          : '<div class="muted">当前任务没有成功下载的媒体文件，只能打开下载目录。</div>';
        const actions = job.status === 'queued'
          ? `<button class="btn" onclick="cancelJob('${job.id}')">取消</button>`
          : '';
        const fileActions = canShowFileActions
          ? `
            <div class="inline">
              <button class="btn soft" onclick="openJobOutput('${job.id}')">打开下载目录</button>
              <div class="menu-anchor split-pill-anchor ${latestMediaPath ? '' : 'disabled'}" data-job-id="${job.id}">
                <button class="split-pill-main" type="button" onclick="openJobFile('${job.id}')" ${latestMediaPath ? '' : 'disabled title="当前任务没有可打开的下载文件"'}>打开文件</button>
                <button class="split-pill-toggle" type="button" aria-label="打开方式" aria-haspopup="menu" aria-expanded="false" onclick="toggleOpenWithMenu('${job.id}', event)" ${latestMediaPath ? '' : 'disabled title="当前任务没有可打开的下载文件"'}><span class="split-pill-chevron" aria-hidden="true">›</span></button>
                <div class="finder-menu" id="open-with-menu-${job.id}" hidden>
                  <button class="finder-menu-item" type="button" disabled>
                    <span>正在读取打开方式…</span>
                  </button>
                </div>
              </div>
              <button class="btn soft" onclick="revealJobFile('${job.id}')" ${latestMediaPath ? '' : 'disabled title="当前任务没有可显示位置的下载文件"'}>显示文件位置</button>
            </div>
            ${fileActionHint}
          `
          : '';
        return `
          <div class="list-item">
            <div class="list-head">
              <div>
                <strong>${job.urls.length} 个链接</strong>
                <div class="muted">${new Date(job.created_at * 1000).toLocaleString()}</div>
              </div>
              <span class="${statusClass}">${statusLabel}</span>
            </div>
            <div class="muted">${job.urls.join('<br>')}</div>
            <div class="inline">
              <span class="badge">成功 ${result.downloaded_items || 0}</span>
              <span class="badge">跳过 ${result.skipped_items || 0}</span>
              <span class="badge ${job.error_message ? 'danger' : ''}">错误 ${result.errors || 0}</span>
              ${actions}
            </div>
            ${fileActions}
            <div class="list-item" style="padding: 12px;">
              <pre>${(job.logs || []).slice(-10).join('\\n') || '暂无日志'}</pre>
            </div>
          </div>
        `;
      }).join('');
    }

    function renderLogs(channels) {
      state.logs = channels;
      const output = document.getElementById('logs-output');
      output.textContent = (channels[state.currentLogChannel] || []).join('\\n') || '暂无日志';
      document.querySelectorAll('[data-log-channel]').forEach((button) => {
        button.classList.toggle('active', button.dataset.logChannel === state.currentLogChannel);
      });
    }

    function renderWrapperStatus(wrapperStatus) {
      state.wrapperStatus = wrapperStatus;
      const copy = document.getElementById('wrapper-status-copy');
      if (!copy) return;
      if (!wrapperStatus) {
        copy.textContent = '正式分发版默认使用 AAC。只有当外部 wrapper 已运行或可启动时，才建议切换到 ALAC 或杜比全景声。';
        return;
      }
      copy.textContent = wrapperStatus.message || '正式分发版默认使用 AAC。只有当外部 wrapper 已运行或可启动时，才建议切换到 ALAC 或杜比全景声。';
    }

    function renderRuntime(runtime) {
      state.runtime = runtime;
      if (!runtime) return;

      const loginButtons = [
        document.getElementById('login-webview-btn'),
        document.getElementById('setup-login-webview-btn'),
      ];
      loginButtons.forEach((button) => {
        button.disabled = !runtime.native_login_supported;
        button.title = runtime.native_login_supported ? '' : runtime.native_login_message;
      });

      document.getElementById('setup-login-copy').textContent = runtime.native_login_message;
      document.getElementById('account-login-copy').textContent = runtime.native_login_supported
        ? '内置登录适合作为主路径；浏览器导入适合作为兜底。两种方式都只读取你自己的 Apple Music 会话，登录态保存在本机私有目录和 Keychain。'
        : `${runtime.native_login_message} 当前这版会把登录态保存在本机私有目录和 Keychain。`;

      const outputInputs = [
        document.getElementById('output-path'),
        document.getElementById('setup-output-path'),
      ];
      outputInputs.forEach((input) => {
        input.readOnly = runtime.folder_picker_supported;
        if (!runtime.folder_picker_supported) {
          input.placeholder = '请输入完整下载目录';
        }
      });

      const pickerButtons = [
        document.getElementById('select-output-btn'),
        document.getElementById('setup-select-output-btn'),
      ];
      pickerButtons.forEach((button) => {
        button.disabled = !runtime.folder_picker_supported;
        button.title = runtime.folder_picker_supported ? '' : runtime.output_path_message;
      });

      document.getElementById('output-path-copy').textContent = runtime.output_path_message;
      document.getElementById('setup-output-path-copy').textContent = runtime.output_path_message;
      document.getElementById('open-file-application-field').style.display = runtime.platform === 'Windows' ? '' : 'none';
      refreshJobs();
    }

    function renderSettings(settings, wrapperStatus = null, runtime = null) {
      state.settings = settings;
      closeOpenWithMenus();
      document.getElementById('output-path').value = settings.output_path || '';
      document.getElementById('setup-output-path').value = settings.output_path || '';
      document.getElementById('log-level').value = settings.log_level || 'INFO';
      document.getElementById('save-cover').value = String(settings.save_cover);
      document.getElementById('song-codec').value = settings.song_codec || 'aac-legacy';
      document.getElementById('overwrite').value = String(settings.overwrite);
      document.getElementById('use-wrapper').value = String(settings.use_wrapper);
      document.getElementById('wrapper-decrypt-ip').value = settings.wrapper_decrypt_ip || DEFAULT_WRAPPER_DECRYPT_IP;
      document.getElementById('open-file-application').value = settings.open_file_application || '';
      document.getElementById('browser-import-enabled').value = String(settings.browser_import_enabled);
      renderRuntime(runtime);
      renderWrapperStatus(wrapperStatus);
      syncSetupOverlay();
    }

    function renderAbout(data) {
      const list = document.getElementById('about-list');
      const browserImports = (data.supported_browser_imports || []).join(', ');
      const kinds = (data.supported_download_kinds || []).join(', ');
      const defaultCodec = data.distribution_audio_default === 'aac-legacy' ? 'AAC' : (data.distribution_audio_default || '未知');
      const originalProjectName = data.original_project_name || 'Gamdl';
      const originalProjectUrl = data.original_project_url || 'https://github.com/glomatico/gamdl';
      const alacMaxSpec = data.alac_max_spec || '24-bit / 192 kHz';
      const alacSpecNote = data.alac_spec_note || '具体取决于歌曲本身是否提供对应规格。';
      const qualityMode = data.alac_mode === 'external-wrapper-only'
        ? 'ALAC / 杜比全景声需要外部 wrapper'
        : (data.alac_mode || '未知');
      const runtime = data.runtime || {};
      list.innerHTML = `
        <div class="list-item"><div class="list-head"><strong>版本</strong><span class="muted">${data.version}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>项目来源</strong><span class="muted">改编自 ${originalProjectName}</span></div><div class="muted">原版项目地址：<a href="${originalProjectUrl}" target="_blank" rel="noreferrer">${originalProjectUrl}</a></div></div>
        <div class="list-item"><div class="list-head"><strong>当前平台</strong><span class="muted">${runtime.platform || 'unknown'}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>可导入的浏览器</strong><span class="muted">${browserImports}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>支持的链接类型</strong><span class="muted">${kinds}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>默认音质</strong><span class="muted">${defaultCodec}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>高音质模式</strong><span class="muted">${qualityMode}</span></div></div>
        <div class="list-item"><div class="list-head"><strong>登录能力</strong><span class="muted">${runtime.native_login_supported ? '内置登录 + 浏览器导入' : '仅浏览器导入'}</span></div><div class="muted">${runtime.native_login_message || ''}</div></div>
        <div class="list-item"><div class="list-head"><strong>ALAC 规格</strong><span class="muted">最高支持 ${alacMaxSpec}</span></div><div class="muted">${alacSpecNote}</div></div>
        <div class="list-item"><div class="list-head"><strong>Wrapper 状态</strong><span class="muted">${data.wrapper_status?.message || 'unknown'}</span></div></div>
      `;
    }

    async function refreshSession() {
      const data = await api('/api/auth/status');
      renderSession(data.session);
    }

    async function refreshSettings() {
      const data = await api('/api/settings');
      renderSettings(data.settings, data.wrapper_status, data.runtime);
    }

    async function refreshJobs(options = {}) {
      const shouldPreserveOpenMenu = Boolean(options.preserveOpenMenu);
      if (shouldPreserveOpenMenu && state.openWithMenuJobId) {
        return;
      }
      closeOpenWithMenus();
      const data = await api('/api/jobs');
      renderJobs(data.jobs);
    }

    async function refreshLogs() {
      const data = await api('/api/logs');
      renderLogs(data.channels);
    }

    async function refreshAbout() {
      const data = await api('/api/about');
      renderAbout(data);
    }

    async function previewLinks() {
      const urlText = document.getElementById('url-input').value;
      const data = await api('/api/preview', {
        method: 'POST',
        body: JSON.stringify({ url_text: urlText }),
      });
      renderPreview(data);
    }

    async function submitJob() {
      const payload = collectSettingsPayload();
      payload.url_text = document.getElementById('url-input').value;
      const data = await api('/api/jobs', {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      showToast(`任务 ${data.id} 已加入队列`);
      document.getElementById('url-input').value = '';
      await refreshJobs();
      activatePage('jobs');
    }

    async function cancelJob(jobId) {
      await api(`/api/jobs/${jobId}/cancel`, { method: 'POST', body: '{}' });
      showToast('已取消排队中的任务');
      refreshJobs();
    }
    window.cancelJob = cancelJob;

    function closeOpenWithMenus() {
      document.querySelectorAll('.finder-menu').forEach((menu) => {
        menu.classList.remove('measuring');
        menu.hidden = true;
      });
      document.querySelectorAll('.split-pill-anchor').forEach((anchor) => {
        anchor.classList.remove('open');
        anchor.classList.remove('loading');
      });
      document.querySelectorAll('.split-pill-toggle').forEach((toggle) => toggle.setAttribute('aria-expanded', 'false'));
      state.openWithMenuRequestId += 1;
      state.openWithMenuJobId = null;
    }
    window.closeOpenWithMenus = closeOpenWithMenus;

    function renderOpenWithMenu(jobId, data) {
      const menu = document.getElementById(`open-with-menu-${jobId}`);
      if (!menu) return;
      const items = data.options || [];
      menu.innerHTML = items.map((item) => {
        if (item.kind === 'separator') {
          return '<div class="finder-menu-separator"></div>';
        }
        const label = escapeHtml(item.label || '');
        const meta = item.is_default ? '<span class="finder-menu-meta">默认</span>' : '';
        if (item.kind === 'default') {
          return `<button class="finder-menu-item" type="button" onclick="executeOpenWithOption('${jobId}', 'default')"><span class="finder-menu-label">${label}</span>${meta}</button>`;
        }
        if (item.kind === 'pick-application') {
          return `<button class="finder-menu-item" type="button" onclick="executeOpenWithOption('${jobId}', 'pick-application')"><span class="finder-menu-label">${label}</span></button>`;
        }
        const applicationPath = encodeURIComponent(item.application_path || '');
        return `<button class="finder-menu-item" type="button" onclick="executeOpenWithOption('${jobId}', 'application', decodeURIComponent('${applicationPath}'))"><span class="finder-menu-label">${label}</span>${meta}</button>`;
      }).join('') || '<button class="finder-menu-item" type="button" disabled><span class="finder-menu-label">没有可用的打开方式</span></button>';
    }

    function revealMeasuredOpenWithMenu(jobId) {
      const menu = document.getElementById(`open-with-menu-${jobId}`);
      if (!menu) return;
      menu.hidden = false;
      menu.classList.add('measuring');
      positionOpenWithMenu(jobId);
      menu.classList.remove('measuring');
    }

    function positionOpenWithMenu(jobId) {
      const menu = document.getElementById(`open-with-menu-${jobId}`);
      const toggle = document.querySelector(`.split-pill-anchor[data-job-id="${jobId}"] .split-pill-toggle`);
      if (!menu || !toggle) return;

      const gap = 0;
      const seamOverlap = 1;
      const margin = 16;
      const desiredMenuWidth = 300;
      const fallbackMinWidth = 150;
      menu.style.width = '';

      const toggleRect = toggle.getBoundingClientRect();
      const viewportWidth = window.innerWidth;
      const viewportHeight = window.innerHeight;
      const maxMenuWidth = Math.max(120, Math.min(360, viewportWidth - margin * 2));
      const availableRightWidth = Math.max(0, viewportWidth - (toggleRect.right - seamOverlap) - gap - margin);
      let computedMenuWidth = Math.min(desiredMenuWidth, maxMenuWidth);
      if (availableRightWidth > 0) {
        computedMenuWidth = Math.min(computedMenuWidth, availableRightWidth);
      }
      if (availableRightWidth >= fallbackMinWidth) {
        computedMenuWidth = Math.max(computedMenuWidth, fallbackMinWidth);
      } else if (availableRightWidth > 0) {
        computedMenuWidth = Math.max(computedMenuWidth, Math.min(availableRightWidth, maxMenuWidth));
      }
      computedMenuWidth = Math.max(120, Math.min(computedMenuWidth, maxMenuWidth));
      menu.style.width = `${computedMenuWidth}px`;

      const sizedMenuRect = menu.getBoundingClientRect();
      const finalMenuWidth = sizedMenuRect.width || computedMenuWidth;
      const menuHeight = sizedMenuRect.height || 220;
      const preferredTop = toggleRect.top - 2;
      const maxLeft = viewportWidth - finalMenuWidth - margin;
      let left = Math.max(margin, Math.min(toggleRect.right - seamOverlap + gap, maxLeft));
      let top = Math.max(margin, Math.min(preferredTop, viewportHeight - menuHeight - margin));

      menu.style.left = `${Math.round(left)}px`;
      menu.style.top = `${Math.round(top)}px`;
    }

    async function toggleOpenWithMenu(jobId, event) {
      try {
        event.stopPropagation();
        const menu = document.getElementById(`open-with-menu-${jobId}`);
        const anchor = event.currentTarget.closest('.split-pill-anchor');
        const toggle = event.currentTarget;
        if (!menu) return;
        const willShow = state.openWithMenuJobId !== jobId || menu.hidden;
        closeOpenWithMenus();
        if (!willShow) {
          return;
        }
        state.openWithMenuJobId = jobId;
        if (anchor) {
          anchor.classList.add('open');
        }
        if (toggle) {
          toggle.setAttribute('aria-expanded', 'true');
        }
        const cached = getCachedOpenWithOptions(jobId);
        if (cached) {
          setOpenWithMenuLoading(jobId, false);
          renderOpenWithMenu(jobId, cached);
          revealMeasuredOpenWithMenu(jobId);
          return;
        }
        setOpenWithMenuLoading(jobId, true);
        menu.classList.remove('measuring');
        menu.hidden = true;
        const requestId = ++state.openWithMenuRequestId;
        const data = await api(`/api/jobs/${jobId}/open-with-options`);
        if (state.openWithMenuJobId !== jobId || state.openWithMenuRequestId !== requestId) {
          return;
        }
        cacheOpenWithOptions(jobId, data.latest_media_path || getLatestMediaPath(jobId), data);
        setOpenWithMenuLoading(jobId, false);
        renderOpenWithMenu(jobId, data);
        revealMeasuredOpenWithMenu(jobId);
      } catch (error) {
        setOpenWithMenuLoading(jobId, false);
        closeOpenWithMenus();
        showToast(error.message || '读取打开方式失败');
      }
    }
    window.toggleOpenWithMenu = toggleOpenWithMenu;

    async function executeOpenWithOption(jobId, kind, applicationPath = null) {
      if (kind === 'default') {
        await openJobFile(jobId);
        return;
      }
      const payload = kind === 'pick-application'
        ? { choose_application: true }
        : { application_path: applicationPath };
      const data = await api(`/api/jobs/${jobId}/open-latest-file`, {
        method: 'POST',
        body: JSON.stringify(payload),
      });
      closeOpenWithMenus();
      if (data.cancelled) {
        return;
      }
      if (kind === 'pick-application') {
        showToast('已使用所选应用打开最近下载文件');
        return;
      }
      showToast('已使用指定应用打开最近下载文件');
    }
    window.executeOpenWithOption = executeOpenWithOption;

    async function openJobOutput(jobId) {
      await api(`/api/jobs/${jobId}/open-output`, { method: 'POST', body: '{}' });
      showToast('已打开下载目录');
    }
    window.openJobOutput = openJobOutput;

    async function openJobFile(jobId) {
      await api(`/api/jobs/${jobId}/open-latest-file`, { method: 'POST', body: '{}' });
      closeOpenWithMenus();
      showToast('已打开最近下载文件');
    }
    window.openJobFile = openJobFile;

    async function revealJobFile(jobId) {
      await api(`/api/jobs/${jobId}/reveal-latest-file`, { method: 'POST', body: '{}' });
      closeOpenWithMenus();
      showToast('已在目录中显示最近下载文件');
    }
    window.revealJobFile = revealJobFile;

    function collectSettingsPayload() {
      return {
        output_path: document.getElementById('output-path').value.trim(),
        log_level: document.getElementById('log-level').value,
        save_cover: document.getElementById('save-cover').value === 'true',
        song_codec: document.getElementById('song-codec').value,
        overwrite: document.getElementById('overwrite').value === 'true',
        use_wrapper: document.getElementById('use-wrapper').value === 'true',
        wrapper_decrypt_ip: document.getElementById('wrapper-decrypt-ip').value.trim(),
        open_file_application: document.getElementById('open-file-application').value.trim(),
        browser_import_enabled: document.getElementById('browser-import-enabled').value === 'true',
      };
    }

    function collectSetupPayload() {
      return {
        output_path: document.getElementById('setup-output-path').value.trim(),
      };
    }

    async function saveSettings() {
      const data = await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify(collectSettingsPayload()),
      });
      renderSettings(data.settings, data.wrapper_status);
      showToast('设置已保存');
    }

    async function chooseOutputFolder(targetInputId, successMessage = '下载目录已确认') {
      const data = await api('/api/desktop/select-folder', {
        method: 'POST',
        body: '{}',
      });
      if (!data.selected) {
        return;
      }
      document.getElementById(targetInputId).value = data.output_path;
      syncSetupOverlay();
      showToast(successMessage);
    }

    async function completeSetup() {
      const payload = collectSetupPayload();
      if (!payload.output_path) {
        throw new Error('请先确认下载目录');
      }
      if (!state.session || !state.session.connected) {
        throw new Error('请先完成 Apple Music 登录');
      }
      const data = await api('/api/settings', {
        method: 'POST',
        body: JSON.stringify({
          output_path: payload.output_path,
          setup_completed: true,
        }),
      });
      renderSettings(data.settings, data.wrapper_status);
      showToast('首次设置已完成');
    }

    async function loginWithWebview() {
      const data = await api('/api/auth/login-webview', {
        method: 'POST',
        body: '{}',
      });
      renderSession(data.session);
      showToast('内置登录完成');
    }

    async function importFromBrowser() {
      const browser = document.getElementById('browser-select').value;
      const data = await api('/api/auth/import-browser', {
        method: 'POST',
        body: JSON.stringify({ browser }),
      });
      renderSession(data.session);
      showToast(`已从 ${browser} 导入登录态`);
    }

    async function setupLoginWithWebview() {
      await loginWithWebview();
    }

    async function setupImportFromBrowser() {
      const browser = document.getElementById('setup-browser-select').value;
      const data = await api('/api/auth/import-browser', {
        method: 'POST',
        body: JSON.stringify({ browser }),
      });
      renderSession(data.session);
      document.getElementById('browser-select').value = browser;
      showToast(`已从 ${browser} 导入登录态`);
    }

    async function logout() {
      const data = await api('/api/auth/logout', {
        method: 'POST',
        body: '{}',
      });
      renderSession(data.session);
      showToast('已退出登录');
    }

    async function exportDiagnostics() {
      const data = await api('/api/diagnostics/export', {
        method: 'POST',
        body: '{}',
      });
      showToast(`诊断包已生成：${data.bundle_path}`);
    }

    function bindEvents() {
      document.addEventListener('click', (event) => {
        if (!event.target.closest('.split-pill-anchor')) {
          closeOpenWithMenus();
        }
      });
      document.addEventListener('keydown', (event) => {
        if (event.key === 'Escape') {
          closeOpenWithMenus();
        }
      });
      document.querySelectorAll('.nav-btn').forEach((button) => {
        button.addEventListener('click', () => activatePage(button.dataset.page));
      });
      document.getElementById('preview-btn').addEventListener('click', () => runAction(previewLinks));
      document.getElementById('submit-job-btn').addEventListener('click', () => runAction(submitJob));
      document.getElementById('refresh-jobs-btn').addEventListener('click', () => runAction(refreshJobs));
      document.getElementById('save-settings-btn').addEventListener('click', () => runAction(saveSettings));
      document.getElementById('select-output-btn').addEventListener('click', () => runAction(() => chooseOutputFolder('output-path', '下载目录已更新')));
      document.getElementById('login-webview-btn').addEventListener('click', () => runAction(loginWithWebview));
      document.getElementById('browser-import-btn').addEventListener('click', () => runAction(importFromBrowser));
      document.getElementById('setup-login-webview-btn').addEventListener('click', () => runAction(setupLoginWithWebview));
      document.getElementById('setup-browser-import-btn').addEventListener('click', () => runAction(setupImportFromBrowser));
      document.getElementById('setup-select-output-btn').addEventListener('click', () => runAction(() => chooseOutputFolder('setup-output-path')));
      document.getElementById('complete-setup-btn').addEventListener('click', () => runAction(completeSetup));
      document.getElementById('logout-btn').addEventListener('click', () => runAction(logout));
      document.getElementById('export-diagnostics-btn').addEventListener('click', () => runAction(exportDiagnostics));
      document.querySelectorAll('[data-log-channel]').forEach((button) => {
        button.addEventListener('click', () => {
          state.currentLogChannel = button.dataset.logChannel;
          renderLogs(state.logs);
        });
      });
    }

    async function runAction(action) {
      try {
        await action();
      } catch (error) {
        console.error(error);
        showToast(error.message);
      }
    }

    async function bootstrap() {
      bindEvents();
      await Promise.all([refreshSession(), refreshSettings(), refreshJobs(), refreshLogs(), refreshAbout()]);
      await previewLinks();
      window.setInterval(() => {
        refreshJobs({ preserveOpenMenu: true }).catch(console.error);
        refreshLogs().catch(console.error);
      }, 2500);
    }

    bootstrap().catch((error) => {
      console.error(error);
      showToast(error.message);
    });
  </script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
