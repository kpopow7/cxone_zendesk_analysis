from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import Engine


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool


def execute_readonly_query(
    engine: Engine,
    sql: str,
    *,
    max_rows: int,
    timeout_seconds: float,
) -> QueryResult:
    timeout_ms = int(timeout_seconds * 1000)
    with engine.begin() as connection:
        connection.execute(text(f"SET LOCAL statement_timeout = '{timeout_ms}ms'"))
        result = connection.execute(text(sql))
        columns = list(result.keys())
        rows: list[dict[str, Any]] = []
        truncated = False
        for index, row in enumerate(result.mappings()):
            if index >= max_rows:
                truncated = True
                break
            rows.append({key: _serialize_value(row[key]) for key in columns})

    return QueryResult(
        columns=columns,
        rows=rows,
        row_count=len(rows),
        truncated=truncated,
    )


def _serialize_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def format_results_for_llm(result: QueryResult, *, max_chars: int = 12000) -> str:
    payload = {
        "columns": result.columns,
        "row_count": result.row_count,
        "truncated": result.truncated,
        "rows": result.rows,
    }
    text = json.dumps(payload, indent=2, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."
