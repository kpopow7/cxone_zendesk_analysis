from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from orchestration.steps.cxone_transcripts import run_cxone_transcript_extraction


@dataclass(frozen=True)
class BackfillChunkResult:
    chunk_index: int
    chunk_start: datetime
    chunk_end: datetime
    records_extracted: int
    records_upserted: int


@dataclass
class HistoricalBackfillResult:
    chunks_completed: int
    records_extracted: int
    records_upserted: int
    chunk_results: list[BackfillChunkResult]


def run_cxone_historical_backfill(
    start: datetime,
    end: datetime,
    *,
    chunk_days: int = 1,
    dry_run: bool = False,
    skip_database: bool = False,
    limit: int | None = None,
) -> HistoricalBackfillResult:
    """One-time enriched load: full analyzed-transcript per segment, processed in date chunks."""
    if chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")

    chunk_results: list[BackfillChunkResult] = []
    total_extracted = 0
    total_upserted = 0
    remaining = limit

    for index, (chunk_start, chunk_end) in enumerate(
        _chunk_date_range(start, end, chunk_days=chunk_days),
        start=1,
    ):
        chunk_limit = remaining
        result = run_cxone_transcript_extraction(
            chunk_start,
            chunk_end,
            dry_run=dry_run,
            skip_database=skip_database,
            enrich_transcripts=True,
            limit=chunk_limit,
        )

        chunk_results.append(
            BackfillChunkResult(
                chunk_index=index,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                records_extracted=result.records_extracted,
                records_upserted=result.records_upserted,
            )
        )
        total_extracted += result.records_extracted
        total_upserted += result.records_upserted

        if remaining is not None:
            remaining -= result.records_extracted
            if remaining <= 0:
                break

    return HistoricalBackfillResult(
        chunks_completed=len(chunk_results),
        records_extracted=total_extracted,
        records_upserted=total_upserted,
        chunk_results=chunk_results,
    )


def _chunk_date_range(
    start: datetime,
    end: datetime,
    *,
    chunk_days: int,
) -> list[tuple[datetime, datetime]]:
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    if start_utc > end_utc:
        return []

    chunks: list[tuple[datetime, datetime]] = []
    cursor = start_utc
    step = timedelta(days=chunk_days)

    while cursor <= end_utc:
        window_end_date = cursor.date() + timedelta(days=chunk_days - 1)
        chunk_end = datetime.combine(window_end_date, datetime.max.time(), tzinfo=timezone.utc)
        chunk_end = min(chunk_end, end_utc)
        chunk_start = max(cursor, start_utc)
        if chunk_start <= chunk_end:
            chunks.append((chunk_start, chunk_end))
        cursor = datetime.combine(
            chunk_end.date() + timedelta(days=1),
            datetime.min.time(),
            tzinfo=timezone.utc,
        )

    return chunks or [(start_utc, end_utc)]


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
