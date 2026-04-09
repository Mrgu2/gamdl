from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class JobCancelledError(Exception):
    result: dict[str, Any] | None = None

    def __str__(self) -> str:
        return "任务已取消。"
