from __future__ import annotations

import argparse
from contextlib import suppress
import json
from pathlib import Path
import socket
import subprocess
import sys
import threading

from gamdl.app import (
    AppLogStore,
    AppPaths,
    AppSettingsStore,
    WrapperManager,
    configure_app_logging,
)
from gamdl.app.downloads import WRAPPER_REQUIRED_CODECS
from gamdl.network import normalize_network_config
from gamdl.interface import SongCodec
from gamdl.macos_login_helper import capture_media_user_token
from gamdl.web_gui import DEFAULT_HOST, DEFAULT_PORT, create_server


def _pick_available_port(host: str, preferred_port: int) -> int:
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with suppress(OSError):
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.bind((host, preferred_port))
            return preferred_port

    with socket.socket(family, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return int(probe.getsockname()[1])


def _run_osascript_picker(script: str, failure_message: str) -> str | None:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(failure_message)
    selected = result.stdout.strip()
    return selected or None


def _select_folder() -> str | None:
    script = """
try
    set chosenFolder to POSIX path of (choose folder with prompt "选择下载目录")
    return chosenFolder
on error number -128
    return ""
end try
"""
    return _run_osascript_picker(script, "无法打开文件夹选择器。")


def _select_input_folder() -> str | None:
    script = """
try
    set chosenFolder to POSIX path of (choose folder with prompt "选择输入目录")
    return chosenFolder
on error number -128
    return ""
end try
"""
    return _run_osascript_picker(script, "无法打开输入目录选择器。")


def _select_file() -> str | None:
    script = """
try
    set chosenFile to POSIX path of (choose file with prompt "选择输入文件")
    return chosenFile
on error number -128
    return ""
end try
"""
    return _run_osascript_picker(script, "无法打开文件选择器。")


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Apple Music Downloader desktop app")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    parser.add_argument("--macos-login-helper", action="store_true")
    parser.add_argument("--macos-login-helper-output")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--timeout", default=300, type=int)
    args = parser.parse_args()

    if getattr(args, "macos_login_helper", False):
        output_path = getattr(args, "macos_login_helper_output", None)
        try:
            payload = capture_media_user_token(
                language=getattr(args, "language", "zh-CN"),
                timeout=getattr(args, "timeout", 300),
            )
        except Exception as exc:
            serialized = json.dumps({"error": str(exc)}, ensure_ascii=False)
            if output_path:
                Path(output_path).write_text(serialized, encoding="utf-8")
            else:
                print(serialized, file=sys.stderr)
            raise SystemExit(1) from exc
        serialized = json.dumps(payload, ensure_ascii=False)
        if output_path:
            Path(output_path).write_text(serialized, encoding="utf-8")
        else:
            print(serialized)
        return

    paths = AppPaths()
    settings_store = AppSettingsStore(paths)
    log_store = AppLogStore()
    settings = settings_store.load()
    configure_app_logging(paths, log_store, settings.log_level)

    if settings.use_wrapper and settings.song_codec in {
        codec.value for codec in WRAPPER_REQUIRED_CODECS
    }:
        try:
            resolved_ip = WrapperManager().ensure_running(
                settings.wrapper_decrypt_ip,
                normalize_network_config(settings.network_mode, settings.proxy_url),
            )
            if resolved_ip != settings.wrapper_decrypt_ip:
                settings = settings_store.save({"wrapper_decrypt_ip": resolved_ip})
        except Exception:
            # Wrapper auto-start is best-effort at app launch; download flow will retry.
            pass

    selected_port = _pick_available_port(args.host, args.port)
    server = create_server(
        args.host,
        selected_port,
        paths=paths,
        log_store=log_store,
        folder_picker=_select_folder,
        file_picker=_select_file,
        input_folder_picker=_select_input_folder,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        import webview  # type: ignore
    except ImportError as exc:
        server.shutdown()
        server.server_close()
        raise RuntimeError(
            "缺少 pywebview，无法启动桌面窗口。请先安装桌面依赖。"
        ) from exc

    url = server.local_url(args.host)
    window = webview.create_window(
        "Apple Music Downloader",
        url=url,
        width=1480,
        height=980,
        min_size=(1180, 760),
        text_select=True,
        background_color="#f6f4ef",
    )
    webview.start(debug=False, http_server=False)

    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


if __name__ == "__main__":
    main()
