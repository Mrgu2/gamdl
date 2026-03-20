from __future__ import annotations

import platform
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DesktopRuntime:
    platform: str
    native_login_supported: bool
    folder_picker_supported: bool
    file_actions_supported: bool
    native_login_message: str
    output_path_message: str
    file_actions_message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_desktop_runtime(
    folder_picker_supported: bool,
    file_actions_supported: bool | None = None,
) -> DesktopRuntime:
    current_platform = platform.system() or "Unknown"
    native_login_supported = current_platform == "Darwin"
    if file_actions_supported is None:
        file_actions_supported = current_platform in {"Darwin", "Windows"}

    if native_login_supported:
        native_login_message = (
            "内置登录是主路径。浏览器导入适合你已经在本机浏览器里登录过 Apple Music 的情况。"
        )
    else:
        native_login_message = (
            f"{current_platform} 版当前不提供内置登录。请先在本机浏览器登录 Apple Music，再使用浏览器导入登录态。"
        )

    if folder_picker_supported:
        output_path_message = "使用“选择文件夹”即可写入下载目录。"
    else:
        output_path_message = (
            f"{current_platform} 版当前不提供原生文件夹选择器。请直接输入完整下载目录。"
        )

    if file_actions_supported:
        file_actions_message = "当前平台支持打开下载目录、打开文件和显示文件位置。"
    else:
        file_actions_message = f"{current_platform} 版当前不支持桌面文件操作。"

    return DesktopRuntime(
        platform=current_platform,
        native_login_supported=native_login_supported,
        folder_picker_supported=folder_picker_supported,
        file_actions_supported=file_actions_supported,
        native_login_message=native_login_message,
        output_path_message=output_path_message,
        file_actions_message=file_actions_message,
    )
