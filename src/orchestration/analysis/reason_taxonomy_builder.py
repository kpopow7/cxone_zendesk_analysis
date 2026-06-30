"""Populate analytics_reason_taxonomy from the configured taxonomy + observed reasons.

Scans every distinct free-text reason in the data (transcript primary reasons + Zendesk
call reasons), resolves each to a canonical label with the deterministic taxonomy, and upserts
the mapping. The analytics views then LEFT JOIN this table to expose canonical reasons. This is
cheap (no LLM) and idempotent, so it can run in the daily pipeline and after a config edit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy
from orchestration.analysis.reasons import normalize_reason_key
from orchestration.db.schema import ensure_reason_taxonomy_table

# Each entry: (source label, table, column). Tables are checked for existence first so a
# partially-built database (e.g. before Step 4b) does not error.
_REASON_SOURCES: tuple[tuple[str, str, str], ...] = (
    ("transcript", "cxone_transcript_analysis", "primary_reason"),
    ("zendesk", "combined_interactions", "call_reason"),
)


@dataclass(frozen=True)
class TaxonomyBuildResult:
    distinct_reasons: int
    mapped_reasons: int
    fallback_reasons: int


def _table_exists(engine: Engine, table: str) -> bool:
    with engine.connect() as connection:
        return connection.execute(text("SELECT to_regclass(:t)"), {"t": table}).scalar() is not None


def _collect_reason_counts(engine: Engine) -> dict[str, dict]:
    """Return {reason_key: {display, count, sources}} across all reason sources."""
    aggregated: dict[str, dict] = {}
    for source_label, table, column in _REASON_SOURCES:
        if not _table_exists(engine, table):
            continue
        query = text(
            f"""
            SELECT {column} AS reason, COUNT(*) AS n
            FROM {table}
            WHERE {column} IS NOT NULL AND btrim({column}) <> ''
            GROUP BY {column}
            """
        )
        with engine.connect() as connection:
            rows = connection.execute(query).mappings().all()
        for row in rows:
            display = str(row["reason"]).strip()
            key = normalize_reason_key(display)
            count = int(row["n"] or 0)
            entry = aggregated.setdefault(
                key, {"display": display, "count": 0, "sources": set()}
            )
            entry["count"] += count
            entry["sources"].add(source_label)
            # Prefer a non-empty display; keep the first reasonable one.
            if not entry["display"] and display:
                entry["display"] = display
    return aggregated


_UPSERT_SQL = """
INSERT INTO analytics_reason_taxonomy (
    reason_key, reason_display, canonical_reason, sources, call_count, updated_at
) VALUES (
    :reason_key, :reason_display, :canonical_reason, :sources, :call_count, :updated_at
)
ON CONFLICT (reason_key) DO UPDATE SET
    reason_display = EXCLUDED.reason_display,
    canonical_reason = EXCLUDED.canonical_reason,
    sources = EXCLUDED.sources,
    call_count = EXCLUDED.call_count,
    updated_at = EXCLUDED.updated_at
"""


def build_reason_taxonomy_map(
    engine: Engine,
    taxonomy: ReasonTaxonomy,
    *,
    prune_missing: bool = True,
) -> TaxonomyBuildResult:
    """Resolve every observed reason to a canonical label and upsert the mapping table.

    When ``prune_missing`` is True, mappings for reasons no longer present in the data are
    deleted so the table stays in sync with the configured vocabulary and current data.
    """
    ensure_reason_taxonomy_table(engine)
    aggregated = _collect_reason_counts(engine)

    now = datetime.now(timezone.utc)
    fallback = taxonomy.fallback_canonical
    mapped = 0
    fallback_count = 0
    seen_keys: list[str] = []

    with engine.begin() as connection:
        for key, entry in aggregated.items():
            canonical = taxonomy.canonicalize(entry["display"]) or fallback
            if canonical == fallback:
                fallback_count += 1
            else:
                mapped += 1
            seen_keys.append(key)
            connection.execute(
                text(_UPSERT_SQL),
                {
                    "reason_key": key,
                    "reason_display": entry["display"][:512],
                    "canonical_reason": canonical,
                    "sources": ",".join(sorted(entry["sources"]))[:64],
                    "call_count": entry["count"],
                    "updated_at": now,
                },
            )
        if prune_missing:
            if seen_keys:
                connection.execute(
                    text(
                        "DELETE FROM analytics_reason_taxonomy "
                        "WHERE reason_key <> ALL(:keys)"
                    ),
                    {"keys": seen_keys},
                )
            else:
                connection.execute(text("DELETE FROM analytics_reason_taxonomy"))

    return TaxonomyBuildResult(
        distinct_reasons=len(aggregated),
        mapped_reasons=mapped,
        fallback_reasons=fallback_count,
    )
