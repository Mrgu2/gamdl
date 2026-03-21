from __future__ import annotations

import platform
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class DesktopRuntime:
    platform: str
    native_login_supported: bool
    folder_picker_supported: bool
    file_picker_supported: bool
    file_actions_supported: bool
    conversion_supported: bool
    native_login_message: str
    output_path_message: str
    input_path_message: str
    file_actions_message: str
    conversion_message: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def detect_desktop_runtime(
    folder_picker_supported: bool,
    file_picker_supported: bool | None = None,
    file_actions_supported: bool | None = None,
    conversion_supported: bool = True,
    conversion_message: str | None = None,
) -> DesktopRuntime:
    current_platform = platform.system() or "Unknown"
    native_login_supported = current_platform == "Darwin"
    if file_picker_supported is None:
        file_picker_supported = folder_picker_supported
    if file_actions_supported is None:
        file_actions_supported = current_platform in {"Darwin", "Windows"}

    if native_login_supported:
        native_login_message = (
            "内置登录是主路径。浏览器导入适合你已经在本机浏览器里登录过 Apple Music 的情况。"
        )
    else:
        native_login_message = (
            f"{current_platform} 版不支持内置登录，请使用浏览器导入。"
        )

    if folder_picker_supported:
        output_path_message = "使用“选择文件夹”即可写入下载目录。"
    else:
        output_path_message = (
            f"{current_platform} 版不支持文件夹选择器，请输入下载目录。"
        )

    if file_picker_supported:
        input_path_message = "可以直接使用“选择文件”或“选择文件夹”填写转换输入路径。"
    else:
        input_path_message = (
            f"{current_platform} 版不支持文件选择器，请输入完整路径。"
        )

    if file_actions_supported:
        file_actions_message = "当前平台支持打开下载目录、打开文件和显示文件位置。"
    else:
        file_actions_message = f"{current_platform} 版当前不支持桌面文件操作。"

    if conversion_message is None:
        conversion_message = (
            "当前版本可用 ffmpeg 转换功能。"
            if conversion_supported
            else "当前环境缺少 ffmpeg，转换功能不可用。"
        )

    return DesktopRuntime(
        platform=current_platform,
        native_login_supported=native_login_supported,
        folder_picker_supported=folder_picker_supported,
        file_picker_supported=file_picker_supported,
        file_actions_supported=file_actions_supported,
        conversion_supported=conversion_supported,
        native_login_message=native_login_message,
        output_path_message=output_path_message,
        input_path_message=input_path_message,
        file_actions_message=file_actions_message,
        conversion_message=conversion_message,
    )
