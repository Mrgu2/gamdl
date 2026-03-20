from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import platform
import shutil
import sys


@dataclass(frozen=True)
class ExecutableResolution:
    name: str
    path: str | None
    source: str

    @property
    def available(self) -> bool:
        return bool(self.path)


def _runtime_root() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            return Path(meipass)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _platform_asset_dir() -> str:
    current_platform = platform.system()
    if current_platform == "Darwin":
        return "macos"
    if current_platform == "Windows":
        return "windows"
    return "linux"


def resolve_executable(name: str, preferred_path: str | None = None) -> ExecutableResolution:
    candidate_names = []
    if preferred_path:
        candidate_names.append(preferred_path)
    candidate_names.append(name)

    binary_name = f"{name}.exe" if platform.system() == "Windows" else name
    bundled_candidates = [
        _runtime_root() / "bin" / binary_name,
        _runtime_root() / "assets" / "bin" / _platform_asset_dir() / binary_name,
        _runtime_root() / "assets" / _platform_asset_dir() / binary_name,
    ]

    for candidate in bundled_candidates:
        if candidate.exists() and candidate.is_file():
            return ExecutableResolution(name=name, path=str(candidate.resolve()), source="bundled")

    for candidate_name in candidate_names:
        resolved = shutil.which(candidate_name)
        if resolved:
            return ExecutableResolution(name=name, path=resolved, source="system")

    return ExecutableResolution(name=name, path=None, source="missing")
