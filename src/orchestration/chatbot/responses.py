from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatbotResponse:
    answer: str
    sql: str | None = None
    row_count: int | None = None
    error: str | None = None
    mode: str = "sql"
    rag_sources: int = 0
    debug: dict = field(default_factory=dict)
