from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from ..cli.utils import CustomLoggerFormatter
from .paths import AppPaths

MAX_LOG_LINES = 800


@dataclass
class LogSnapshot:
    channel: str
    lines: list[str]


class AppLogStore(logging.Handler):
    def __init__(self, max_lines: int = MAX_LOG_LINES) -> None:
        super().__init__()
        self.max_lines = max_lines
        self.channels = {
            "app": deque(maxlen=max_lines),
            "auth": deque(maxlen=max_lines),
            "download": deque(maxlen=max_lines),
        }
        self.setFormatter(CustomLoggerFormatter(use_colors=False))

    def emit(self, record: logging.LogRecord) -> None:
        message = self.format(record)
        channel = "app"
        if record.name.startswith("gamdl.app.auth"):
            channel = "auth"
        elif record.name.startswith("gamdl.app.download"):
            channel = "download"
        self.channels[channel].append(message)

    def snapshot(self) -> list[LogSnapshot]:
        return [
            LogSnapshot(channel=name, lines=list(lines))
            for name, lines in self.channels.items()
        ]

    def export(self) -> dict[str, list[str]]:
        return {name: list(lines) for name, lines in self.channels.items()}


class SafeStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            super().emit(record)
        except ValueError:
            # Test harnesses can close captured stderr/stdout before late logs flush.
            return

    def handleError(self, record: logging.LogRecord) -> None:
        return


def _file_handler(path: Path) -> logging.FileHandler:
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(CustomLoggerFormatter(use_colors=False))
    return handler


def configure_app_logging(paths: AppPaths, log_store: AppLogStore, level: str) -> None:
    paths.ensure()

    root_logger = logging.getLogger("gamdl")
    root_logger.setLevel(level)
    root_logger.propagate = False

    for handler in list(root_logger.handlers):
        root_logger.removeHandler(handler)
        handler.close()

    stream_handler = SafeStreamHandler()
    stream_handler.setFormatter(CustomLoggerFormatter())
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(log_store)
    root_logger.addHandler(_file_handler(paths.logs_dir / "app.log"))

    auth_logger = logging.getLogger("gamdl.app.auth")
    download_logger = logging.getLogger("gamdl.app.download")
    for logger, filename in (
        (auth_logger, "auth.log"),
        (download_logger, "download.log"),
    ):
        logger.setLevel(level)
        logger.propagate = True
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        logger.addHandler(_file_handler(paths.logs_dir / filename))
