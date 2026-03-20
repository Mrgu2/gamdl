from __future__ import annotations

import os
from pathlib import Path
import platform
import subprocess
from typing import Any


class DesktopFileActions:
    def __init__(self, current_platform: str | None = None) -> None:
        self.platform = current_platform or platform.system() or "Unknown"

    @property
    def supported(self) -> bool:
        return self.platform in {"Darwin", "Windows"}

    def open_output(self, path: str) -> None:
        target = self._require_existing_path(path, "目录")
        if not target.is_dir():
            raise RuntimeError("下载目录不是有效文件夹。")
        self._open_path(target)

    def open_file(self, path: str, application: str | None = None) -> None:
        target = self._require_existing_path(path, "文件")
        if not target.is_file():
            raise RuntimeError("最近下载文件不存在。")
        self._open_path(target, application=application)

    def reveal_file(self, path: str) -> None:
        target = self._require_existing_path(path, "文件")
        if not target.is_file():
            raise RuntimeError("最近下载文件不存在。")
        self._reveal_path(target)

    def get_open_with_options(
        self,
        path: str,
        configured_application: str | None = None,
    ) -> list[dict[str, Any]]:
        target = self._require_existing_path(path, "文件")
        if not target.is_file():
            raise RuntimeError("最近下载文件不存在。")
        if self.platform == "Darwin":
            return self._mac_open_with_options(target)
        if self.platform == "Windows":
            return self._windows_open_with_options(configured_application)
        self._ensure_supported()
        return []

    def choose_application(self) -> str | None:
        if self.platform == "Darwin":
            return self._mac_choose_application()
        if self.platform == "Windows":
            return None
        self._ensure_supported()
        return None

    def _require_existing_path(self, path: str, kind: str) -> Path:
        self._ensure_supported()
        target = Path(path).expanduser()
        if not target.exists():
            raise RuntimeError(f"{kind}不存在，可能已被移动或删除。")
        return target.resolve()

    def _ensure_supported(self) -> None:
        if not self.supported:
            raise RuntimeError(f"当前平台 {self.platform} 暂不支持桌面文件操作。")

    def _open_path(self, path: Path, application: str | None = None) -> None:
        try:
            if self.platform == "Darwin":
                if application:
                    subprocess.run(["open", "-a", application, str(path)], check=True)
                else:
                    subprocess.run(["open", str(path)], check=True)
                return
            if self.platform == "Windows":
                if application:
                    subprocess.run([application, str(path)], check=True)
                else:
                    startfile = getattr(os, "startfile", None)
                    if startfile is None:
                        raise RuntimeError("当前环境缺少 Windows 文件打开能力。")
                    startfile(str(path))
                return
            self._ensure_supported()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"系统未能打开目标路径：{exc}") from exc

    def _reveal_path(self, path: Path) -> None:
        try:
            if self.platform == "Darwin":
                subprocess.run(["open", "-R", str(path)], check=True)
                return
            if self.platform == "Windows":
                subprocess.run(["explorer", f"/select,{path}"], check=True)
                return
            self._ensure_supported()
        except (OSError, subprocess.CalledProcessError) as exc:
            raise RuntimeError(f"系统未能显示目标文件位置：{exc}") from exc

    def _mac_open_with_options(self, path: Path) -> list[dict[str, Any]]:
        default_application, applications = self._mac_lookup_applications(path)
        options: list[dict[str, Any]] = []
        seen_paths: set[str] = set()

        if default_application:
            options.append(
                {
                    "label": default_application["label"],
                    "kind": "application",
                    "is_default": True,
                    "application_path": default_application["application_path"],
                }
            )
            seen_paths.add(default_application["application_path"])

        other_applications = sorted(
            (
                app
                for app in applications
                if app["application_path"] not in seen_paths
            ),
            key=lambda app: app["label"].lower(),
        )
        if options and other_applications:
            options.append({"kind": "separator"})
        for app in other_applications:
            seen_paths.add(app["application_path"])
            options.append(
                {
                    "label": app["label"],
                    "kind": "application",
                    "is_default": False,
                    "application_path": app["application_path"],
                }
            )
        if options:
            options.append({"kind": "separator"})
        options.append(
            {
                "label": "其他…",
                "kind": "pick-application",
                "is_default": False,
                "application_path": None,
            }
        )
        return options

    def _windows_open_with_options(
        self,
        configured_application: str | None = None,
    ) -> list[dict[str, Any]]:
        options: list[dict[str, Any]] = [
            {
                "label": "系统默认应用",
                "kind": "default",
                "is_default": True,
                "application_path": None,
            }
        ]
        configured_application = (configured_application or "").strip()
        if configured_application:
            options.append({"kind": "separator"})
            options.append(
                {
                    "label": f"自定义应用 ({configured_application})",
                    "kind": "application",
                    "is_default": False,
                    "application_path": configured_application,
                }
            )
        return options

    def _mac_lookup_applications(
        self,
        path: Path,
    ) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
        try:
            from AppKit import NSWorkspace, NSURL
        except ImportError as exc:
            raise RuntimeError("当前环境缺少 macOS 原生应用查询能力。") from exc

        workspace = NSWorkspace.sharedWorkspace()
        file_url = NSURL.fileURLWithPath_(str(path))
        default_url = workspace.URLForApplicationToOpenURL_(file_url)
        application_urls = list(workspace.URLsForApplicationsToOpenURL_(file_url) or [])
        default_application = self._mac_application_option(default_url)
        applications = [
            option
            for option in (
                self._mac_application_option(application_url)
                for application_url in application_urls
            )
            if option is not None
        ]
        if default_application and all(
            app["application_path"] != default_application["application_path"]
            for app in applications
        ):
            applications.append(default_application)
        return default_application, applications

    @staticmethod
    def _mac_application_option(application_url) -> dict[str, str] | None:
        if application_url is None:
            return None
        application_path = str(application_url.path() or "").strip()
        if not application_path:
            return None
        return {
            "label": Path(application_path).stem,
            "application_path": application_path,
        }

    def _mac_choose_application(self) -> str | None:
        try:
            result = subprocess.run(
                [
                    "osascript",
                    "-e",
                    "POSIX path of (choose application)",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").lower()
            if "user canceled" in stderr or "128" in stderr:
                return None
            raise RuntimeError(f"无法打开应用选择器：{exc.stderr or exc}") from exc

        selected_path = (result.stdout or "").strip()
        return selected_path or None
