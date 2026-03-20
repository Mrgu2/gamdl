from __future__ import annotations

import argparse
import json
import sys
import time
from threading import Event


def capture_media_user_token(language: str = "zh-CN", timeout: int = 300) -> dict[str, float | str]:
    try:
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyRegular,
            NSBackingStoreBuffered,
            NSMakeRect,
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
            self.window.makeKeyAndOrderFront_(None)
            app.activateIgnoringOtherApps_(True)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture Apple Music login from a macOS webview")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()
    print(json.dumps(capture_media_user_token(args.language, args.timeout), ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
