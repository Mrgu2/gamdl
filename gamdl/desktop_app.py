from __future__ import annotations

import argparse
from contextlib import suppress
import socket
import subprocess
import threading

from gamdl.app import (
    AppLogStore,
    AppPaths,
    AppSettingsStore,
    WrapperManager,
    configure_app_logging,
)
from gamdl.interface import SongCodec
from gamdl.web_gui import DEFAULT_HOST, DEFAULT_PORT, create_server


def _pick_available_port(host: str, preferred_port: int) -> int:
    with suppress(OSError):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, preferred_port))
            return preferred_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
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
    args = parser.parse_args()

    paths = AppPaths()
    settings_store = AppSettingsStore(paths)
    log_store = AppLogStore()
    settings = settings_store.load()
    configure_app_logging(paths, log_store, settings.log_level)

    if settings.use_wrapper and settings.song_codec == SongCodec.ALAC.value:
        try:
            resolved_ip = WrapperManager().ensure_running(settings.wrapper_decrypt_ip)
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

    url = f"http://{args.host}:{server.server_address[1]}"
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


if __name__ == "__main__":
    main()
