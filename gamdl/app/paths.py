from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


def restrict_permissions(path: Path, mode: int) -> None:
    try:
        path.chmod(mode)
    except OSError:
        return


def _downloads_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return downloads if downloads.exists() else Path.home()


@dataclass(frozen=True)
class AppPaths:
    app_name: str = "Apple Music Downloader"
    default_downloads_folder_name: str = "Apple Music Downloader"
    base_dir: Path | None = None

    @property
    def app_support_dir(self) -> Path:
        if self.base_dir is not None:
            return self.base_dir / self.app_name
        return Path.home() / "Library" / "Application Support" / self.app_name

    @property
    def logs_dir(self) -> Path:
        return self.app_support_dir / "logs"

    @property
    def diagnostics_dir(self) -> Path:
        return self.app_support_dir / "diagnostics"

    @property
    def settings_path(self) -> Path:
        return self.app_support_dir / "settings.json"

    @property
    def session_path(self) -> Path:
        return self.app_support_dir / "session.json"

    @property
    def token_fallback_path(self) -> Path:
        return self.app_support_dir / ".token"

    @property
    def default_output_path(self) -> Path:
        return _downloads_dir() / self.default_downloads_folder_name

    @property
    def temp_dir(self) -> Path:
        return self.app_support_dir / "tmp"

    def ensure(self) -> None:
        for path in (
            self.app_support_dir,
            self.logs_dir,
            self.diagnostics_dir,
            self.temp_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
            restrict_permissions(path, 0o700)
