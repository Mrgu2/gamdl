from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from ipaddress import ip_address
from pathlib import Path
from typing import Any
import uuid

from .paths import AppPaths
from .wrapper_manager import (
    default_wrapper_decrypt_ip,
    normalize_wrapper_decrypt_ip,
    parse_wrapper_decrypt_ip,
)

ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR"}
ALLOWED_SONG_CODECS = {"aac-legacy", "aac", "alac", "atmos"}
BOOLEAN_KEYS = {
    "overwrite",
    "save_cover",
    "browser_import_enabled",
    "setup_completed",
    "use_wrapper",
}
STRING_KEYS = {
    "output_path",
    "log_level",
    "last_login_method",
    "open_file_application",
    "song_codec",
    "theme",
    "wrapper_decrypt_ip",
}
CLEARABLE_STRING_KEYS = {"open_file_application"}
ALLOWED_THEMES = {"warm", "cool"}
DEFAULT_WRAPPER_DECRYPT_IP = default_wrapper_decrypt_ip()
WRAPPER_DECRYPT_IP_ERROR = (
    "Wrapper 解密地址格式无效，请使用类似 127.0.0.1:10022 的地址，或填写 1 到 55535 之间的端口号。"
)
WRAPPER_LOCALHOST_ONLY_ERROR = "桌面版 wrapper 地址必须是本机回环地址，例如 127.0.0.1:10022。"


@dataclass
class AppSettings:
    output_path: str
    overwrite: bool = False
    save_cover: bool = True
    log_level: str = "INFO"
    last_login_method: str | None = None
    open_file_application: str = ""
    browser_import_enabled: bool = True
    setup_completed: bool = False
    song_codec: str = "aac-legacy"
    theme: str = "warm"
    use_wrapper: bool = False
    wrapper_decrypt_ip: str = DEFAULT_WRAPPER_DECRYPT_IP
    artist_auto_select: str = ""


class AppSettingsStore:
    def __init__(self, paths: AppPaths | None = None) -> None:
        self.paths = paths or AppPaths()
        self.path = self.paths.settings_path

    def defaults(self) -> AppSettings:
        return AppSettings(output_path=str(self.paths.default_output_path))

    def validate_output_path(self, output_path: str) -> str:
        if not output_path.strip():
            raise ValueError("请先选择下载目录。")

        candidate = Path(output_path).expanduser()

        if candidate.exists() and not candidate.is_dir():
            raise ValueError("下载目录不是文件夹，请重新选择。")

        try:
            candidate.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建下载目录：{candidate}") from exc

        if not candidate.is_dir():
            raise ValueError("下载目录不是文件夹，请重新选择。")

        probe_path = candidate / f".apple-music-downloader-write-test-{uuid.uuid4().hex}"
        try:
            probe_path.write_text("", encoding="utf-8")
            probe_path.unlink()
        except OSError as exc:
            raise ValueError("这个目录没有写权限，请换一个可写位置。") from exc

        normalized = candidate if candidate.is_absolute() else candidate.absolute()
        return str(normalized)

    def _sanitize(self, payload: dict[str, Any]) -> dict[str, Any]:
        sanitized: dict[str, Any] = {}
        for key, value in payload.items():
            if key in BOOLEAN_KEYS and isinstance(value, bool):
                sanitized[key] = value
            elif key in STRING_KEYS and isinstance(value, str):
                stripped = value.strip()
                if stripped or key in CLEARABLE_STRING_KEYS:
                    sanitized[key] = stripped

        if "log_level" in sanitized and sanitized["log_level"] not in ALLOWED_LOG_LEVELS:
            sanitized.pop("log_level")
        if "song_codec" in sanitized and sanitized["song_codec"] not in ALLOWED_SONG_CODECS:
            sanitized.pop("song_codec")
        if "theme" in sanitized and sanitized["theme"] not in ALLOWED_THEMES:
            sanitized.pop("theme")
        if "wrapper_decrypt_ip" in sanitized:
            try:
                sanitized["wrapper_decrypt_ip"] = self._validate_wrapper_decrypt_ip(
                    sanitized["wrapper_decrypt_ip"]
                )
            except ValueError:
                sanitized.pop("wrapper_decrypt_ip")
        return sanitized

    @staticmethod
    def _validate_wrapper_decrypt_ip(wrapper_decrypt_ip: str) -> str:
        normalized = normalize_wrapper_decrypt_ip(wrapper_decrypt_ip)
        host, _port = parse_wrapper_decrypt_ip(normalized)
        if host.lower() == "localhost":
            return normalized
        try:
            parsed_host = ip_address(host)
        except ValueError as exc:
            raise ValueError(WRAPPER_LOCALHOST_ONLY_ERROR) from exc
        if not parsed_host.is_loopback:
            raise ValueError(WRAPPER_LOCALHOST_ONLY_ERROR)
        return normalized

    def load(self) -> AppSettings:
        defaults = self.defaults()
        if not self.path.exists():
            return defaults
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return defaults
        if not isinstance(raw, dict):
            return defaults

        payload = asdict(defaults)
        payload.update(self._sanitize(raw))
        return AppSettings(**payload)

    def save(self, payload: dict[str, Any]) -> AppSettings:
        if isinstance(payload.get("wrapper_decrypt_ip"), str):
            wrapper_decrypt_ip = payload["wrapper_decrypt_ip"].strip()
            if wrapper_decrypt_ip:
                try:
                    self._validate_wrapper_decrypt_ip(wrapper_decrypt_ip)
                except ValueError as exc:
                    message = str(exc)
                    if message == WRAPPER_LOCALHOST_ONLY_ERROR:
                        raise ValueError(message) from exc
                    raise ValueError(WRAPPER_DECRYPT_IP_ERROR) from exc

        settings = asdict(self.load())
        settings.update(self._sanitize(payload))
        settings["output_path"] = self.validate_output_path(settings["output_path"])
        self.paths.ensure()
        self.path.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return AppSettings(**settings)
