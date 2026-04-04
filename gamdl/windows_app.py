from __future__ import annotations

import argparse
from contextlib import suppress
import platform
import socket
import threading
import webbrowser

from gamdl.app import (
    AppLogStore,
    AppPaths,
    AppSettingsStore,
    WrapperManager,
    configure_app_logging,
)
from gamdl.app.downloads import WRAPPER_REQUIRED_CODECS
from gamdl.interface import SongCodec
from gamdl.network import normalize_network_config
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


class WindowsLauncher:
    def __init__(self, url: str, on_exit, public_origin: str | None = None) -> None:
        try:
            import tkinter as tk
            from tkinter import ttk
        except ImportError as exc:
            raise RuntimeError("缺少 tkinter，无法启动 Windows 桌面启动器。") from exc

        self._tk = tk
        self._url = url
        self._public_origin = public_origin or url
        self._on_exit = on_exit
        self.root = tk.Tk()
        self.root.title("Apple Music Downloader")
        self.root.geometry("520x240")
        self.root.resizable(False, False)
        self.root.configure(background="#f6f4ef")
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        frame = ttk.Frame(self.root, padding=18)
        frame.pack(fill="both", expand=True)

        ttk.Label(
            frame,
            text="Apple Music Downloader",
            font=("Segoe UI", 16, "bold"),
        ).pack(anchor="w")
        ttk.Label(
            frame,
            text=(
                "Windows 版当前通过默认浏览器承载下载界面。\n"
                "浏览器辅助登录暂不提供，请先在本机浏览器登录 Apple Music，"
                "再使用“导入浏览器登录态”。"
            ),
            justify="left",
        ).pack(anchor="w", pady=(10, 8))
        ttk.Label(
            frame,
            text=f"会话地址: {self._url}",
            foreground="#5f6368",
            justify="left",
        ).pack(anchor="w", pady=(0, 14))

        actions = ttk.Frame(frame)
        actions.pack(anchor="w")
        ttk.Button(actions, text="打开应用", command=self.open_browser).pack(side="left")
        ttk.Button(actions, text="退出", command=self.close).pack(side="left", padx=(10, 0))

    def open_browser(self) -> None:
        webbrowser.open(self._url, new=1)

    def close(self) -> None:
        if self.root.winfo_exists():
            self.root.after(0, self.root.destroy)
        self._on_exit()

    def run(self) -> None:
        self.open_browser()
        self.root.mainloop()


def main() -> None:
    if platform.system() != "Windows":
        raise RuntimeError("`gamdl.windows_app` 仅用于 Windows 打包和运行。")

    parser = argparse.ArgumentParser(description="Run the Apple Music Downloader Windows launcher")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", default=DEFAULT_PORT, type=int)
    args = parser.parse_args()

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
                settings_store.save({"wrapper_decrypt_ip": resolved_ip})
        except Exception:
            pass

    selected_port = _pick_available_port(args.host, args.port)
    server = create_server(
        args.host,
        selected_port,
        paths=paths,
        log_store=log_store,
        folder_picker=None,
        file_picker=None,
        input_folder_picker=None,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    shutdown_lock = threading.Lock()
    shutdown_called = False

    def shutdown() -> None:
        nonlocal shutdown_called
        with shutdown_lock:
            if shutdown_called:
                return
            shutdown_called = True
        server.shutdown()
        server.server_close()

    url = server.local_url(args.host)
    launcher = WindowsLauncher(url, shutdown, public_origin=server.public_origin(args.host))

    try:
        launcher.run()
    finally:
        shutdown()
        thread.join(timeout=2)


if __name__ == "__main__":
    main()
