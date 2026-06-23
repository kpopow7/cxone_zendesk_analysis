"""Compare analytic tables across two Postgres databases for sync parity.

Used to verify that the local pipeline output has been faithfully replicated to
the Railway database the hosted chatbot reads from. For each table we compare
row counts, business-date ranges, freshness (max updated_at), and a per-day row
count breakdown so missing/partial days surface clearly.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.db.schema import (
    CombinedInteractionRow,
    CxoneTranscriptAnalysisRow,
    CxoneTranscriptRow,
    ZendeskTicketCommentRow,
    ZendeskTicketRow,
)


@dataclass(frozen=True)
class TableAuditSpec:
    """How to audit one table.

    date_column   - the business/event date used for range + per-day checks.
    updated_column - the freshness timestamp bumped on every upsert.
    """

    name: str
    date_column: str
    updated_column: str
    model: Any


# Keep this aligned with TABLE_MODELS / TABLE_SYNC_TIMESTAMP_COLUMN in
# scripts/sync_to_railway.py. zendesk_ticket_comments is included because the
# chatbot can read it even though it is synced by a separate script.
AUDIT_SPECS: dict[str, TableAuditSpec] = {
    "cxone_transcripts": TableAuditSpec(
        name="cxone_transcripts",
        date_column="interaction_start",
        updated_column="updated_at",
        model=CxoneTranscriptRow,
    ),
    "cxone_transcript_analysis": TableAuditSpec(
        name="cxone_transcript_analysis",
        date_column="analyzed_at",
        updated_column="updated_at",
        model=CxoneTranscriptAnalysisRow,
    ),
    "zendesk_tickets": TableAuditSpec(
        name="zendesk_tickets",
        date_column="created_at",
        updated_column="row_updated_at",
        model=ZendeskTicketRow,
    ),
    "combined_interactions": TableAuditSpec(
        name="combined_interactions",
        date_column="interaction_start",
        updated_column="updated_at",
        model=CombinedInteractionRow,
    ),
    "zendesk_ticket_comments": TableAuditSpec(
        name="zendesk_ticket_comments",
        date_column="created_at",
        updated_column="row_updated_at",
        model=ZendeskTicketCommentRow,
    ),
}

# Tables that the daily Railway sync keeps in lock-step (see sync_to_railway.py).
DEFAULT_AUDIT_TABLES: tuple[str, ...] = (
    "combined_interactions",
    "zendesk_tickets",
    "cxone_transcripts",
    "cxone_transcript_analysis",
)


@dataclass(frozen=True)
class TableSnapshot:
    """Aggregate stats for one table from one database."""

    table: str
    exists: bool
    total: int = 0
    null_date_count: int = 0
    min_date: datetime | None = None
    max_date: datetime | None = None
    max_updated: datetime | None = None
    daily_counts: dict[date, int] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class DailyDelta:
    day: date
    source_count: int
    target_count: int

    @property
    def delta(self) -> int:
        return self.target_count - self.source_count


@dataclass(frozen=True)
class TableComparison:
    table: str
    source: TableSnapshot
    target: TableSnapshot
    daily_deltas: tuple[DailyDelta, ...]

    @property
    def count_delta(self) -> int:
        return self.target.total - self.source.total

    @property
    def missing_in_target_days(self) -> int:
        """Days present in source but absent (or short) in target."""
        return sum(1 for d in self.daily_deltas if d.delta < 0)

    @property
    def extra_in_target_days(self) -> int:
        return sum(1 for d in self.daily_deltas if d.delta > 0)

    @property
    def date_range_matches(self) -> bool:
        return (
            _same_instant(self.source.min_date, self.target.min_date)
            and _same_instant(self.source.max_date, self.target.max_date)
        )

    @property
    def status(self) -> str:
        if self.source.error or self.target.error:
            return "ERROR"
        if not self.source.exists or not self.target.exists:
            return "MISSING_TABLE"
        if self.count_delta == 0 and not self.daily_deltas and self.date_range_matches:
            return "OK"
        return "MISMATCH"


def _same_instant(left: datetime | None, right: datetime | None) -> bool:
    if left is None and right is None:
        return True
    if left is None or right is None:
        return False
    return left == right


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def fetch_table_snapshot(
    engine: Engine,
    spec: TableAuditSpec,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    statement_timeout_ms: int = 60_000,
) -> TableSnapshot:
    """Read aggregate + per-day stats for one table.

    When a window is given, totals/range/daily counts cover only rows whose
    date_column falls inside [start, end]; null_date_count is always reported
    over the full table since null-dated rows cannot be window-compared.
    """
    from sqlalchemy import inspect as sa_inspect

    if not sa_inspect(engine).has_table(spec.name):
        return TableSnapshot(table=spec.name, exists=False)

    where_parts: list[str] = []
    params: dict[str, Any] = {}
    if start is not None:
        where_parts.append(f"{spec.date_column} >= :start")
        params["start"] = start
    if end is not None:
        where_parts.append(f"{spec.date_column} <= :end")
        params["end"] = end
    where_clause = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    summary_sql = text(
        f"""
        SELECT
            count(*) AS total,
            count(*) FILTER (WHERE {spec.date_column} IS NULL) AS null_dates,
            min({spec.date_column}) AS min_date,
            max({spec.date_column}) AS max_date,
            max({spec.updated_column}) AS max_updated
        FROM {spec.name}
        {where_clause}
        """
    )
    # null_date_count over the entire table, independent of any window.
    null_sql = text(
        f"SELECT count(*) FROM {spec.name} WHERE {spec.date_column} IS NULL"
    )
    daily_sql = text(
        f"""
        SELECT date_trunc('day', {spec.date_column}) AS day, count(*) AS n
        FROM {spec.name}
        WHERE {spec.date_column} IS NOT NULL
        {('AND ' + ' AND '.join(where_parts)) if where_parts else ''}
        GROUP BY 1
        ORDER BY 1
        """
    )

    try:
        with engine.connect() as conn:
            conn.execute(text(f"SET statement_timeout = {int(statement_timeout_ms)}"))
            summary = conn.execute(summary_sql, params).mappings().one()
            null_total = conn.execute(null_sql).scalar_one()
            daily_rows = conn.execute(daily_sql, params).all()
    except Exception as exc:  # noqa: BLE001 - surfaced in the report, not fatal
        return TableSnapshot(table=spec.name, exists=True, error=str(exc))

    daily_counts: dict[date, int] = {}
    for day_value, n in daily_rows:
        day = _as_date(day_value)
        if day is not None:
            daily_counts[day] = int(n)

    return TableSnapshot(
        table=spec.name,
        exists=True,
        total=int(summary["total"] or 0),
        null_date_count=int(null_total or 0),
        min_date=summary["min_date"],
        max_date=summary["max_date"],
        max_updated=summary["max_updated"],
        daily_counts=daily_counts,
    )


def compare_snapshots(
    spec: TableAuditSpec,
    source: TableSnapshot,
    target: TableSnapshot,
) -> TableComparison:
    """Pure diff of two snapshots; safe to unit test without a database."""
    deltas: list[DailyDelta] = []
    for day in sorted(set(source.daily_counts) | set(target.daily_counts)):
        src_n = source.daily_counts.get(day, 0)
        tgt_n = target.daily_counts.get(day, 0)
        if src_n != tgt_n:
            deltas.append(DailyDelta(day=day, source_count=src_n, target_count=tgt_n))
    return TableComparison(
        table=spec.name,
        source=source,
        target=target,
        daily_deltas=tuple(deltas),
    )


def audit_sync(
    source_engine: Engine,
    target_engine: Engine,
    *,
    table_names: list[str] | None = None,
    start: datetime | None = None,
    end: datetime | None = None,
    statement_timeout_ms: int = 60_000,
) -> list[TableComparison]:
    """Compare the requested tables between two databases."""
    names = table_names or list(DEFAULT_AUDIT_TABLES)
    comparisons: list[TableComparison] = []
    for name in names:
        spec = AUDIT_SPECS[name]
        source = fetch_table_snapshot(
            source_engine, spec, start=start, end=end, statement_timeout_ms=statement_timeout_ms
        )
        target = fetch_table_snapshot(
            target_engine, spec, start=start, end=end, statement_timeout_ms=statement_timeout_ms
        )
        comparisons.append(compare_snapshots(spec, source, target))
    return comparisons


def all_in_sync(comparisons: list[TableComparison]) -> bool:
    return all(c.status == "OK" for c in comparisons)
