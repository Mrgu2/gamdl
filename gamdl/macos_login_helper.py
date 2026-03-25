from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from threading import Event


def capture_media_user_token(language: str = "zh-CN", timeout: int = 300) -> dict[str, float | str]:
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivateAllWindows,
            NSApplicationActivateIgnoringOtherApps,
            NSApplicationActivationPolicyRegular,
            NSBackingStoreBuffered,
            NSMakeRect,
            NSRunningApplication,
            NSWindow,
            NSWindowStyleMaskClosable,
            NSWindowStyleMaskMiniaturizable,
            NSWindowStyleMaskResizable,
            NSWindowStyleMaskTitled,
        )
        from Foundation import NSURL, NSURLRequest
        from PyObjCTools import AppHelper
        from WebKit import WKWebView, WKWebViewConfiguration
    except ImportError as exc:
        raise RuntimeError(
            "缺少 PyObjC WebKit 依赖，无法启动内置登录窗口。"
        ) from exc

    class LoginController:
        def __init__(self) -> None:
            self.result = None
            self.error = None
            self.finished = Event()
            self.closed = False

        def activate_window(self) -> None:
            self.window.orderFrontRegardless()
            self.window.makeKeyAndOrderFront_(None)
            self.window.makeMainWindow()
            app = NSApplication.sharedApplication()
            app.unhide_(None)
            app.activateIgnoringOtherApps_(True)
            current_app = NSRunningApplication.currentApplication()
            current_app.activateWithOptions_(
                NSApplicationActivateIgnoringOtherApps | NSApplicationActivateAllWindows
            )

        def build_window(self) -> None:
            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
            style_mask = (
                NSWindowStyleMaskTitled
                | NSWindowStyleMaskClosable
                | NSWindowStyleMaskMiniaturizable
                | NSWindowStyleMaskResizable
            )
            self.window = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
                NSMakeRect(0, 0, 1160, 820),
                style_mask,
                NSBackingStoreBuffered,
                False,
            )
            self.window.setTitle_("登录 Apple Music")
            configuration = WKWebViewConfiguration.alloc().init()
            self.webview = WKWebView.alloc().initWithFrame_configuration_(
                self.window.contentView().bounds(),
                configuration,
            )
            self.webview.setAutoresizingMask_(18)
            self.window.contentView().addSubview_(self.webview)
            request_url = NSURL.URLWithString_("https://music.apple.com/login")
            request = NSURLRequest.requestWithURL_(request_url)
            self.webview.loadRequest_(request)
            self.window.center()
            self.activate_window()
            AppHelper.callLater(0.2, self.activate_window)
            AppHelper.callLater(1.0, self.activate_window)
            AppHelper.callLater(1.0, self.poll_cookie_store)
            AppHelper.callLater(float(timeout), self.finish)

        def poll_cookie_store(self) -> None:
            if self.finished.is_set():
                return
            cookie_store = self.webview.configuration().websiteDataStore().httpCookieStore()

            def handle_cookies(cookies) -> None:
                for cookie in cookies:
                    if cookie.name() == "media-user-token":
                        self.result = {
                            "media_user_token": str(cookie.value()),
                            "captured_at": time.time(),
                        }
                        self.finish()
                        return
                AppHelper.callLater(1.0, self.poll_cookie_store)

            cookie_store.getAllCookies_(handle_cookies)

        def finish(self) -> None:
            if self.window:
                self.window.orderOut_(None)
            self.finished.set()
            AppHelper.stopEventLoop()

    controller = LoginController()
    AppHelper.callAfter(controller.build_window)
    AppHelper.runConsoleEventLoop(installInterrupt=True, mode="default")

    if not controller.result:
        raise RuntimeError("内置登录未完成，未获取到 media-user-token。")

    return controller.result


def build_login_helper_command(
    language: str = "zh-CN",
    timeout: int = 300,
    output_path: Path | None = None,
) -> list[str]:
    if getattr(sys, "frozen", False):
        app_bundle = Path(sys.executable).resolve().parents[2]
        helper_app = app_bundle / "Contents" / "Helpers" / "Apple Music Login Helper.app"
        command = ["open", "-n", "-W", "-a", str(helper_app), "--args"]
    else:
        command = [sys.executable, "-m", "gamdl.macos_login_helper"]
    command.extend(["--language", language, "--timeout", str(timeout)])
    if output_path is not None:
        command.extend(["--output", str(output_path)])
    return command


def _helper_error_message(stderr: str, stdout: str) -> str:
    for candidate in (stderr, stdout):
        lines = [line.strip() for line in candidate.splitlines() if line.strip()]
        if not lines:
            continue
        message = lines[-1]
        if ": " in message:
            label, detail = message.split(": ", 1)
            if label.endswith(("Error", "Exception")) and detail:
                return detail
        return message
    return "内置登录进程异常退出。"


def capture_media_user_token_isolated(
    language: str = "zh-CN",
    timeout: int = 300,
) -> dict[str, float | str]:
    with tempfile.NamedTemporaryFile(suffix="-macos-login.json", delete=False) as handle:
        output_path = Path(handle.name)

    try:
        completed = subprocess.run(
            build_login_helper_command(language=language, timeout=timeout, output_path=output_path),
            capture_output=True,
            text=True,
            check=False,
            cwd=str(Path(__file__).resolve().parents[1]),
        )
        if not output_path.exists():
            if completed.returncode != 0:
                raise RuntimeError(_helper_error_message(completed.stderr, completed.stdout))
            raise RuntimeError("内置登录进程未返回结果。")
        raw_payload = output_path.read_text(encoding="utf-8").strip()
        if not raw_payload:
            if completed.returncode != 0:
                raise RuntimeError(_helper_error_message(completed.stderr, completed.stdout))
            raise RuntimeError("内置登录进程返回了空结果。")
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            if completed.returncode != 0:
                raise RuntimeError(_helper_error_message(completed.stderr, completed.stdout)) from exc
            raise RuntimeError("内置登录进程返回了无效结果。") from exc
        if payload.get("error"):
            raise RuntimeError(str(payload["error"]))
        if "media_user_token" not in payload:
            if completed.returncode != 0:
                raise RuntimeError(_helper_error_message(completed.stderr, completed.stdout))
            raise RuntimeError("内置登录结果缺少 media-user-token。")
        return payload
    finally:
        output_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Apple Music login from a macOS webview")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--output")
    args = parser.parse_args()
    try:
        payload = capture_media_user_token(args.language, args.timeout)
    except Exception as exc:
        if args.output:
            Path(args.output).write_text(
                json.dumps({"error": str(exc)}, ensure_ascii=False),
                encoding="utf-8",
            )
        raise
    serialized = json.dumps(payload, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(serialized, encoding="utf-8")
    else:
        print(serialized)


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
