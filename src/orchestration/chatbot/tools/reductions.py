from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine


def get_reduction_recommendations(
    *,
    engine: Engine,
    reasons: list[str] | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Fetch ranked reduction recommendations from the latest report run.

    The underlying view is phone-transcript-wide (not per ticket form). When reasons are
    provided, returns rows whose primary_reason matches any of them (case-insensitive).
    """
    limit = max(1, min(limit, 50))
    reason_list = [r.strip() for r in (reasons or []) if r and str(r).strip()]

    if reason_list:
        clauses = " OR ".join(f"primary_reason ILIKE :r{i}" for i in range(len(reason_list)))
        params: dict[str, Any] = {f"r{i}": f"%{reason}%" for i, reason in enumerate(reason_list)}
        params["lim"] = limit
        stmt = text(
            f"SELECT rank, primary_reason, call_count, share_pct, recommendation_source,\n"
            f"       recommendations_text, reduction_hints\n"
            f"FROM analytics_reduction_recommendations\n"
            f"WHERE {clauses}\n"
            f"ORDER BY rank\n"
            f"LIMIT :lim"
        )
    else:
        params = {"lim": limit}
        stmt = text(
            "SELECT rank, primary_reason, call_count, share_pct, recommendation_source,\n"
            "       recommendations_text, reduction_hints\n"
            "FROM analytics_reduction_recommendations\n"
            "ORDER BY rank\n"
            "LIMIT :lim"
        )

    try:
        with engine.connect() as connection:
            rows = [dict(row._mapping) for row in connection.execute(stmt, params)]
    except Exception as exc:
        return {"error": str(exc), "recommendations": [], "note": _SCOPE_NOTE}

    return {
        "recommendation_count": len(rows),
        "recommendations": rows,
        "note": _SCOPE_NOTE,
    }


_SCOPE_NOTE = (
    "Reduction recommendations come from the latest phone-transcript reduction report "
    "(analytics_reduction_recommendations). They are not scoped by ticket form type; "
    "match by reason name is best-effort."
)
