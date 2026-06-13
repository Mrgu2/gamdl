from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .. import __version__
from .logging_utils import AppLogStore
from .paths import AppPaths
from .settings import AppSettingsStore

REDACTED = "***"
DIAGNOSTIC_SETTINGS_KEYS = (
    "log_level",
    "language",
    "browser_import_enabled",
    "last_login_method",
    "song_codec",
    "use_wrapper",
    "wrapper_decrypt_ip",
    "network_mode",
    "proxy_url",
)


def _redact_proxy_url(proxy_url: str) -> str:
    if not proxy_url:
        return proxy_url
    parsed = urlsplit(proxy_url)
    if parsed.username is None and parsed.password is None:
        return proxy_url

    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if parsed.port is not None:
        host = f"{host}:{parsed.port}"
    netloc = f"{REDACTED}:{REDACTED}@{host}"
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _redact_text(value: str) -> str:
    redacted = value
    redacted = re.sub(
        r"(?i)\btoken:\s*[^\s,;]+",
        f"Token: {REDACTED}",
        redacted,
    )
    redacted = re.sub(
        r"(?i)\bbearer\s+[^\s,;]+",
        f"Bearer {REDACTED}",
        redacted,
    )
    redacted = re.sub(
        r"(media-user-token[\"'=:\s]+)([^\s\",]+)",
        rf"\1{REDACTED}",
        redacted,
        flags=re.IGNORECASE,
    )
    return redacted


def _sanitize_settings_payload(payload: dict) -> dict:
    sanitized = {
        key: payload[key]
        for key in DIAGNOSTIC_SETTINGS_KEYS
        if key in payload
    }
    proxy_url = sanitized.get("proxy_url")
    if isinstance(proxy_url, str):
        sanitized["proxy_url"] = _redact_proxy_url(proxy_url)
    return sanitized


def _sanitize_log_payload(payload: dict[str, list[str]]) -> dict[str, list[str]]:
    return {
        channel: [_redact_text(line) for line in lines]
        for channel, lines in payload.items()
    }


class DiagnosticsService:
    def __init__(
        self,
        paths: AppPaths,
        settings_store: AppSettingsStore,
        log_store: AppLogStore,
    ) -> None:
        self.paths = paths
        self.settings_store = settings_store
        self.log_store = log_store

    def export_bundle(self) -> Path:
        self.paths.ensure()
        bundle_path = self.paths.diagnostics_dir / f"gamdl-diagnostics-{int(time.time())}.zip"
        settings_payload = _sanitize_settings_payload(self.settings_store.load().__dict__)
        in_memory_logs = _sanitize_log_payload(self.log_store.export())
        with zipfile.ZipFile(bundle_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(
                "metadata.json",
                json.dumps(
                    {
                        "version": __version__,
                        "created_at": time.time(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            archive.writestr(
                "settings.json",
                json.dumps(settings_payload, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "logs/in-memory.json",
                json.dumps(in_memory_logs, ensure_ascii=False, indent=2),
            )
            for log_file in self.paths.logs_dir.glob("*.log"):
                if log_file.is_symlink() or not log_file.is_file():
                    continue
                archive.writestr(
                    f"logs/{log_file.name}",
                    _redact_text(log_file.read_text(encoding="utf-8")),
                )
        return bundle_path
