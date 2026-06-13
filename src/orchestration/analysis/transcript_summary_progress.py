from __future__ import annotations

import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable


@dataclass
class TranscriptSummaryProgress:
    """Write human-visible progress to stderr (flushed immediately)."""

    emit: Callable[[str], None]

    @classmethod
    def stderr(cls) -> TranscriptSummaryProgress:
        def _emit(message: str) -> None:
            stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
            print(f"[{stamp}] {message}", file=sys.stderr, flush=True)

        return cls(emit=_emit)

    def info(self, message: str) -> None:
        self.emit(message)

    def error(self, message: str) -> None:
        self.emit(f"ERROR: {message}")
