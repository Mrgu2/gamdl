from __future__ import annotations

import json
import time
import zipfile
from pathlib import Path

from .. import __version__
from .logging_utils import AppLogStore
from .paths import AppPaths
from .settings import AppSettingsStore


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
                json.dumps(self.settings_store.load().__dict__, ensure_ascii=False, indent=2),
            )
            archive.writestr(
                "logs/in-memory.json",
                json.dumps(self.log_store.export(), ensure_ascii=False, indent=2),
            )
            for log_file in self.paths.logs_dir.glob("*.log"):
                archive.write(log_file, f"logs/{log_file.name}")
        return bundle_path
