from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from orchestration.analysis.call_selection import (
    CallSelectionOverrides,
    exclusion_summary,
    row_matches_segment_filters,
)
from orchestration.analysis.importance import score_reason_bucket
from orchestration.analysis.llm_client import validate_openai_api_key
from orchestration.analysis.transcript_summary_progress import TranscriptSummaryProgress
from orchestration.analysis.reasons import is_negative_sentiment, normalize_reason_key
from orchestration.analysis.recommendations import recommendations_for_reason
from orchestration.analysis.timeframes import TimeWindow, iter_time_window_chunks
from orchestration.analysis.transcript_reason_llm import (
    TranscriptClassificationError,
    TranscriptReasonAnalysis,
    classify_transcript,
    reason_keys,
)
from orchestration.analysis.transcript_reduction_llm import (
    TranscriptReductionError,
    generate_primary_reason_reductions,
    samples_from_analyses,
)
from orchestration.analysis.transcript_summary_config import (
    TranscriptSummaryConfig,
    load_transcript_summary_config,
)
from orchestration.config import Settings
from orchestration.db.analytics_views import ensure_analytics_views
from orchestration.db.schema import (
    CxoneTranscriptAnalysisRow,
    CxoneTranscriptRow,
    ensure_transcript_analysis_table,
)
from orchestration.db.session import get_engine, get_session_factory

logger = logging.getLogger(__name__)

_CACHE_LOOKUP_BATCH_SIZE = 400


@dataclass
class TertiaryBreakdownItem:
    tertiary_reason: str
    count: int
    share_of_secondary_pct: float


@dataclass
class SecondaryBreakdownItem:
    secondary_reason: str
    count: int
    share_of_primary_pct: float
    tertiary: list[TertiaryBreakdownItem] = field(default_factory=list)


@dataclass
class PrimaryReasonBucket:
    primary_reason: str
    primary_key: str
    count: int
    share_pct: float
    importance_score: float
    negative_sentiment_pct: float
    secondary: list[SecondaryBreakdownItem] = field(default_factory=list)
    sample_summaries: list[str] = field(default_factory=list)
    sample_segment_ids: list[str] = field(default_factory=list)
    reduction_hints: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    recommendation_source: str = "rules"


@dataclass
class TranscriptSummaryReport:
    generated_at: str
    timeframe: dict[str, Any]
    filters: dict[str, Any]
    totals: dict[str, int | float]
    classification: dict[str, Any]
    top_primary_reasons: list[PrimaryReasonBucket] = field(default_factory=list)
    insights: list[str] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _ClassifiedSegment:
    segment_id: str
    analysis: TranscriptReasonAnalysis
    client_sentiment: str | None
    skill_name: str | None


@dataclass
class _TranscriptRunStats:
    transcripts_in_window: int = 0
    transcripts_with_text: int = 0
    classified_new: int = 0
    classify_errors: int = 0
    persisted: int = 0


def run_transcript_summary(
    settings: Settings,
    *,
    time_window: TimeWindow,
    config_path: Path | None = None,
    config: TranscriptSummaryConfig | None = None,
    selection_overrides: CallSelectionOverrides | None = None,
    use_reduction_llm: bool | None = None,
    reanalyze: bool = False,
    sample_limit: int | None = None,
    batch_size: int | None = None,
    chunk_days: int | None = None,
    commit_every: int | None = None,
    classify_only: bool = False,
    progress: TranscriptSummaryProgress | None = None,
) -> TranscriptSummaryReport:
    if config is None:
        path = config_path or Path(settings.transcript_summary_config_path)
        config = load_transcript_summary_config(path, selection_overrides=selection_overrides)

    if sample_limit is not None:
        from dataclasses import replace

        config = replace(
            config,
            classification=replace(
                config.classification,
                sample_limit=sample_limit,
            ),
        )

    api_key = settings.openai_api_key
    if not api_key:
        raise ValueError("OPENAI_API_KEY is required for transcript summary (LLM classification)")

    progress = progress or TranscriptSummaryProgress.stderr()
    validate_openai_api_key(
        api_key=api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=min(settings.request_timeout_seconds, 30.0),
    )
    progress.info("OpenAI API key validated.")

    ensure_transcript_analysis_table(settings.database_url)

    session_factory = get_session_factory(settings.database_url)
    if batch_size is not None and batch_size < 1:
        raise ValueError("batch_size must be at least 1")
    if chunk_days is not None and chunk_days < 1:
        raise ValueError("chunk_days must be at least 1")
    if commit_every is not None and commit_every < 1:
        raise ValueError("commit_every must be at least 1")

    in_batch_mode = batch_size is not None or chunk_days is not None
    effective_commit_every = commit_every if commit_every is not None else (10 if in_batch_mode else None)
    effective_classify_only = classify_only or in_batch_mode

    if chunk_days is not None:
        return _run_transcript_summary_by_date_chunks(
            session_factory,
            time_window=time_window,
            config=config,
            settings=settings,
            api_key=api_key,
            use_reduction_llm=use_reduction_llm,
            reanalyze=reanalyze,
            batch_size=batch_size,
            chunk_days=chunk_days,
            commit_every=effective_commit_every or 10,
            classify_only=effective_classify_only,
            progress=progress,
        )

    with session_factory() as session:
        if batch_size is None:
            rows = _fetch_transcripts(session, time_window)
            return _build_report(
                session,
                rows,
                time_window=time_window,
                config=config,
                settings=settings,
                api_key=api_key,
                use_reduction_llm=use_reduction_llm,
                reanalyze=reanalyze,
                batch_size=None,
                progress=progress,
            )

        return _build_report_batched(
            session,
            time_window=time_window,
            config=config,
            settings=settings,
            api_key=api_key,
            use_reduction_llm=use_reduction_llm,
            reanalyze=reanalyze,
            batch_size=batch_size,
            commit_every=effective_commit_every or 10,
            classify_only=effective_classify_only,
            progress=progress,
        )


def _fetch_transcripts(
    session: Session,
    time_window: TimeWindow,
) -> list[CxoneTranscriptRow]:
    stmt = _transcript_query_for_window(time_window)
    return list(session.scalars(stmt).all())


def _transcript_query_for_window(time_window: TimeWindow):
    stmt = select(CxoneTranscriptRow)
    if time_window.start is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start <= time_window.end)
    return stmt


def _run_transcript_summary_by_date_chunks(
    session_factory,
    *,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    use_reduction_llm: bool | None,
    reanalyze: bool,
    batch_size: int | None,
    chunk_days: int,
    commit_every: int,
    classify_only: bool,
    progress: TranscriptSummaryProgress,
) -> TranscriptSummaryReport:
    """Classify transcripts in consecutive date windows; commit frequently within each."""
    chunks = iter_time_window_chunks(time_window, chunk_days)
    effective_batch_size = batch_size if batch_size is not None else 50
    combined_stats = _TranscriptRunStats()
    chunk_summaries: list[str] = []

    progress.info(
        f"Date-chunked run: {len(chunks)} chunk(s) of up to {chunk_days} day(s), "
        f"batch_size={effective_batch_size}, commit_every={commit_every}"
    )

    for index, chunk_window in enumerate(chunks, start=1):
        progress.info(
            f"Date chunk {index}/{len(chunks)}: {chunk_window.label}"
        )
        with session_factory() as session:
            chunk_report = _build_report_batched(
                session,
                time_window=chunk_window,
                config=config,
                settings=settings,
                api_key=api_key,
                use_reduction_llm=False,
                reanalyze=reanalyze,
                batch_size=effective_batch_size,
                commit_every=commit_every,
                classify_only=True,
                progress=progress,
            )

        totals = chunk_report.totals
        combined_stats.transcripts_in_window += int(totals.get("transcripts_in_window", 0))
        combined_stats.transcripts_with_text += int(totals.get("transcripts_with_text", 0))
        combined_stats.classified_new += int(totals.get("transcripts_classified_this_run", 0))
        combined_stats.classify_errors += int(totals.get("classification_error_count", 0))
        combined_stats.persisted += int(totals.get("transcripts_persisted_this_run", 0))
        chunk_summaries.append(
            f"{chunk_window.label}: classified {totals.get('transcripts_classified_this_run', 0)}, "
            f"persisted {totals.get('transcripts_persisted_this_run', 0)}, "
            f"errors {totals.get('classification_error_count', 0)}"
        )

    with session_factory() as session:
        if combined_stats.persisted:
            ensure_analytics_views(get_engine(settings.database_url))

        if classify_only:
            report = _minimal_report(
                session,
                stats=combined_stats,
                time_window=time_window,
                config=config,
                settings=settings,
                batch_size=effective_batch_size,
                chunk_days=chunk_days,
                commit_every=commit_every,
                use_reduction_llm=use_reduction_llm,
            )
        else:
            segments = _load_classified_segments_from_db(session, time_window, config)
            report = _assemble_report(
                session,
                segments=segments,
                stats=combined_stats,
                time_window=time_window,
                config=config,
                settings=settings,
                api_key=api_key,
                use_reduction_llm=use_reduction_llm,
                batch_size=effective_batch_size,
                chunk_days=chunk_days,
                commit_every=commit_every,
            )

    report.insights.insert(
        0,
        f"Processed {len(chunks)} date chunk(s); committed to cxone_transcript_analysis "
        f"every {commit_every} successful classification(s).",
    )
    for summary in chunk_summaries:
        report.insights.append(summary)

    return report


def _load_classified_segments_from_db(
    session: Session,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
) -> list[_ClassifiedSegment]:
    """Load analysis rows for report aggregation (no transcript text)."""
    stmt = (
        select(CxoneTranscriptRow, CxoneTranscriptAnalysisRow)
        .join(
            CxoneTranscriptAnalysisRow,
            CxoneTranscriptAnalysisRow.segment_id == CxoneTranscriptRow.segment_id,
        )
    )
    if time_window.start is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start <= time_window.end)

    segments: list[_ClassifiedSegment] = []
    for transcript_row, analysis_row in session.execute(stmt).yield_per(500):
        if not row_matches_segment_filters(transcript_row, config.call_selection):
            continue
        segments.append(
            _ClassifiedSegment(
                segment_id=transcript_row.segment_id,
                analysis=_analysis_from_db_row(analysis_row),
                client_sentiment=transcript_row.client_sentiment,
                skill_name=transcript_row.skill_name,
            )
        )
    return segments


def _apply_segment_filters_sql(stmt, filters):
    direction = filters.call_direction.strip().lower()
    if direction == "inbound":
        normalized = func.upper(func.replace(CxoneTranscriptRow.call_direction, "-", "_"))
        stmt = stmt.where(or_(normalized.like("%IN_BOUND%"), normalized == "INBOUND"))
    elif direction == "outbound":
        normalized = func.upper(func.replace(CxoneTranscriptRow.call_direction, "-", "_"))
        stmt = stmt.where(or_(normalized.like("%OUT_BOUND%"), normalized == "OUTBOUND"))

    if filters.media_types_include:
        stmt = stmt.where(CxoneTranscriptRow.media_type.in_(list(filters.media_types_include)))
    if filters.media_types_exclude:
        stmt = stmt.where(
            CxoneTranscriptRow.media_type.notin_(list(filters.media_types_exclude))
        )
    if filters.skills_include:
        stmt = stmt.where(CxoneTranscriptRow.skill_name.in_(list(filters.skills_include)))
    if filters.skills_exclude:
        stmt = stmt.where(
            CxoneTranscriptRow.skill_name.notin_(list(filters.skills_exclude))
        )
    if filters.teams_include:
        stmt = stmt.where(CxoneTranscriptRow.team_name.in_(list(filters.teams_include)))
    if filters.teams_exclude:
        stmt = stmt.where(CxoneTranscriptRow.team_name.notin_(list(filters.teams_exclude)))
    return stmt


def _work_queue_stmt(
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    *,
    reanalyze: bool,
    cursor: str,
):
    stmt = select(CxoneTranscriptRow)
    if time_window.start is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start <= time_window.end)
    stmt = _apply_segment_filters_sql(stmt, config.call_selection)
    stmt = stmt.where(func.length(func.trim(CxoneTranscriptRow.transcript_text)) > 0)

    skip_existing = config.classification.skip_existing and not reanalyze
    if skip_existing:
        stmt = stmt.outerjoin(
            CxoneTranscriptAnalysisRow,
            CxoneTranscriptAnalysisRow.segment_id == CxoneTranscriptRow.segment_id,
        ).where(CxoneTranscriptAnalysisRow.segment_id.is_(None))

    if cursor:
        stmt = stmt.where(CxoneTranscriptRow.segment_id > cursor)
    return stmt.order_by(CxoneTranscriptRow.segment_id.asc())


def _count_work_queue(
    session: Session,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    *,
    reanalyze: bool,
) -> int:
    stmt = select(func.count()).select_from(CxoneTranscriptRow)
    if time_window.start is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start <= time_window.end)
    stmt = _apply_segment_filters_sql(stmt, config.call_selection)
    stmt = stmt.where(func.length(func.trim(CxoneTranscriptRow.transcript_text)) > 0)

    skip_existing = config.classification.skip_existing and not reanalyze
    if skip_existing:
        stmt = stmt.outerjoin(
            CxoneTranscriptAnalysisRow,
            CxoneTranscriptAnalysisRow.segment_id == CxoneTranscriptRow.segment_id,
        ).where(CxoneTranscriptAnalysisRow.segment_id.is_(None))

    return int(session.scalar(stmt) or 0)


def _analysis_row_count(session: Session) -> int:
    return int(session.scalar(select(func.count()).select_from(CxoneTranscriptAnalysisRow)) or 0)


def _iter_work_batches(
    session: Session,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    batch_size: int,
    *,
    reanalyze: bool,
) -> Iterator[list[CxoneTranscriptRow]]:
    """Yield transcript rows still needing classification (skips cached rows in SQL)."""
    cursor = ""
    while True:
        stmt = _work_queue_stmt(
            time_window,
            config,
            reanalyze=reanalyze,
            cursor=cursor,
        ).limit(batch_size)
        batch = list(session.scalars(stmt).all())
        if not batch:
            break
        yield batch
        cursor = batch[-1].segment_id
        session.expunge_all()


def _filter_transcript_rows(
    rows: list[CxoneTranscriptRow],
    config: TranscriptSummaryConfig,
    *,
    sample_remaining: int | None,
) -> tuple[list[CxoneTranscriptRow], int | None]:
    """Return filtered rows and updated sample budget."""
    filtered: list[CxoneTranscriptRow] = []
    remaining = sample_remaining
    for row in rows:
        if not row_matches_segment_filters(row, config.call_selection):
            continue
        if not (row.transcript_text or "").strip():
            continue
        if remaining is not None:
            if remaining <= 0:
                break
            remaining -= 1
        filtered.append(row)
    return filtered, remaining


def _build_report_batched(
    session: Session,
    *,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    use_reduction_llm: bool | None,
    reanalyze: bool,
    batch_size: int,
    commit_every: int,
    classify_only: bool,
    progress: TranscriptSummaryProgress,
) -> TranscriptSummaryReport:
    stats = _TranscriptRunStats()
    sample_remaining = config.classification.sample_limit
    batch_index = 0
    stop = False

    pending = _count_work_queue(session, time_window, config, reanalyze=reanalyze)
    table_before = _analysis_row_count(session)
    progress.info(
        f"Window {time_window.label}: {pending} transcript(s) need classification "
        f"(cxone_transcript_analysis currently has {table_before} rows)."
    )
    if pending == 0:
        progress.info("Nothing to classify in this window — already complete or filtered out.")
        return _minimal_report(
            session,
            stats=stats,
            time_window=time_window,
            config=config,
            settings=settings,
            batch_size=batch_size,
            chunk_days=None,
            commit_every=commit_every,
            use_reduction_llm=use_reduction_llm,
        )

    for batch_rows in _iter_work_batches(
        session,
        time_window,
        config,
        batch_size,
        reanalyze=reanalyze,
    ):
        if stop:
            break
        batch_index += 1
        stats.transcripts_in_window += len(batch_rows)
        stats.transcripts_with_text += len(batch_rows)

        filtered, sample_remaining = _filter_transcript_rows(
            batch_rows,
            config,
            sample_remaining=sample_remaining,
        )

        classified_new, errors, persisted = _classify_and_commit_incremental(
            session,
            filtered,
            config=config,
            settings=settings,
            api_key=api_key,
            commit_every=commit_every,
            progress=progress,
        )
        stats.classified_new += classified_new
        stats.classify_errors += errors
        stats.persisted += persisted

        table_now = _analysis_row_count(session)
        progress.info(
            f"Batch {batch_index}: queued={len(batch_rows)} classify_attempted={len(filtered)} "
            f"new={classified_new} errors={errors} committed={persisted} "
            f"table_total={table_now}"
        )

        if sample_remaining is not None and sample_remaining <= 0:
            stop = True

    if stats.persisted:
        ensure_analytics_views(get_engine(settings.database_url))

    if classify_only:
        return _minimal_report(
            session,
            stats=stats,
            time_window=time_window,
            config=config,
            settings=settings,
            batch_size=batch_size,
            chunk_days=None,
            commit_every=commit_every,
            use_reduction_llm=use_reduction_llm,
        )

    segments = _load_classified_segments_from_db(session, time_window, config)
    return _assemble_report(
        session,
        segments=segments,
        stats=stats,
        time_window=time_window,
        config=config,
        settings=settings,
        api_key=api_key,
        use_reduction_llm=use_reduction_llm,
        batch_size=batch_size,
        commit_every=commit_every,
    )


def _classify_and_commit_incremental(
    session: Session,
    rows: list[CxoneTranscriptRow],
    *,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    commit_every: int,
    progress: TranscriptSummaryProgress,
) -> tuple[int, int, int]:
    """Classify rows one-by-one; commit every ``commit_every`` successes."""
    if not rows:
        return 0, 0, 0

    classified_new = 0
    classify_errors = 0
    persisted = 0
    pending: dict[str, TranscriptReasonAnalysis] = {}

    for index, row in enumerate(rows, start=1):
        try:
            analysis = classify_transcript(
                transcript_text=row.transcript_text or "",
                segment_summary=row.segment_summary,
                client_sentiment=row.client_sentiment,
                skill_name=row.skill_name,
                agent_name=row.agent_name,
                api_key=api_key,
                model=settings.openai_model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                max_transcript_chars=config.classification.max_transcript_chars,
            )
        except Exception as exc:
            classify_errors += 1
            progress.error(f"{row.segment_id}: {exc}")
            continue

        classified_new += 1
        pending[row.segment_id] = analysis

        if len(pending) >= commit_every and config.classification.store_results:
            _persist_analyses(session, pending, model=settings.openai_model)
            session.commit()
            persisted += len(pending)
            progress.info(f"Committed {len(pending)} row(s) to cxone_transcript_analysis")
            pending.clear()

        if index % max(1, commit_every) == 0:
            progress.info(
                f"  progress: {index}/{len(rows)} in current fetch batch "
                f"({classified_new} ok, {classify_errors} errors)"
            )

    if pending and config.classification.store_results:
        _persist_analyses(session, pending, model=settings.openai_model)
        session.commit()
        persisted += len(pending)
        progress.info(f"Committed final {len(pending)} row(s) to cxone_transcript_analysis")

    return classified_new, classify_errors, persisted


def _minimal_report(
    session: Session,
    *,
    stats: _TranscriptRunStats,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    settings: Settings,
    batch_size: int | None,
    chunk_days: int | None,
    commit_every: int | None,
    use_reduction_llm: bool | None,
) -> TranscriptSummaryReport:
    table_total = _analysis_row_count(session)
    totals = {
        "transcripts_in_window": stats.transcripts_in_window,
        "transcripts_with_text": stats.transcripts_with_text,
        "transcripts_analyzed": table_total,
        "transcripts_classified_this_run": stats.classified_new,
        "transcripts_persisted_this_run": stats.persisted,
        "transcripts_reused_from_cache": 0,
        "classification_error_count": stats.classify_errors,
        "summaries_in_database": table_total,
    }
    classification_meta: dict[str, Any] = {
        "max_transcript_chars": config.classification.max_transcript_chars,
        "concurrency": config.classification.concurrency,
        "store_results": config.classification.store_results,
        "skip_existing": config.classification.skip_existing,
        "sample_limit": config.classification.sample_limit,
        "classify_only": True,
    }
    if batch_size is not None:
        classification_meta["batch_size"] = batch_size
    if chunk_days is not None:
        classification_meta["chunk_days"] = chunk_days
    if commit_every is not None:
        classification_meta["commit_every"] = commit_every

    reduction_requested = (
        use_reduction_llm
        if use_reduction_llm is not None
        else config.reduction_recommendations.enabled
    )

    return TranscriptSummaryReport(
        generated_at=datetime.now(timezone.utc).isoformat(),
        timeframe={
            "preset": time_window.preset,
            "start": time_window.start.isoformat() if time_window.start else None,
            "end": time_window.end.isoformat() if time_window.end else None,
            "label": time_window.label,
        },
        filters=config.call_selection.to_dict(),
        totals=totals,
        classification=classification_meta,
        top_primary_reasons=[],
        insights=[
            f"Classified {stats.classified_new} transcript(s) this run; "
            f"persisted {stats.persisted}; errors {stats.classify_errors}.",
            f"cxone_transcript_analysis now has {table_total} row(s) total.",
        ],
        llm={
            "classification_model": settings.openai_model,
            "segments_classified_this_run": stats.classified_new,
            "classification_errors": stats.classify_errors,
            "reduction_llm_requested": reduction_requested,
            "reduction_llm_applied": False,
            "reduction_reasons_processed": 0,
        },
    )


def _classify_and_persist_batch(
    session: Session,
    filtered: list[CxoneTranscriptRow],
    *,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    reanalyze: bool,
    progress: TranscriptSummaryProgress | None = None,
) -> tuple[list[_ClassifiedSegment], int, int, int]:
    if not filtered:
        return [], 0, 0, 0

    progress = progress or TranscriptSummaryProgress.stderr()
    classified_new, classify_errors, persisted = _classify_and_commit_incremental(
        session,
        filtered,
        config=config,
        settings=settings,
        api_key=api_key,
        commit_every=1,
        progress=progress,
    )

    segment_ids = {row.segment_id for row in filtered}
    existing = _load_cached_analyses(session, segment_ids)
    segments: list[_ClassifiedSegment] = []
    for row in filtered:
        analysis = existing.get(row.segment_id)
        if analysis is None:
            continue
        segments.append(
            _ClassifiedSegment(
                segment_id=row.segment_id,
                analysis=analysis,
                client_sentiment=row.client_sentiment,
                skill_name=row.skill_name,
            )
        )

    return segments, classified_new, classify_errors, persisted


def _fetch_transcript_texts(session: Session, segment_ids: set[str]) -> dict[str, str]:
    if not segment_ids:
        return {}

    texts: dict[str, str] = {}
    ids = list(segment_ids)
    for offset in range(0, len(ids), _CACHE_LOOKUP_BATCH_SIZE):
        batch = ids[offset : offset + _CACHE_LOOKUP_BATCH_SIZE]
        stmt = select(CxoneTranscriptRow.segment_id, CxoneTranscriptRow.transcript_text).where(
            CxoneTranscriptRow.segment_id.in_(batch)
        )
        for segment_id, transcript_text in session.execute(stmt).all():
            texts[str(segment_id)] = transcript_text or ""
    return texts


def _build_report(
    session: Session,
    rows: list[CxoneTranscriptRow],
    *,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    use_reduction_llm: bool | None,
    reanalyze: bool,
    batch_size: int | None = None,
    progress: TranscriptSummaryProgress | None = None,
) -> TranscriptSummaryReport:
    progress = progress or TranscriptSummaryProgress.stderr()
    filtered, _ = _filter_transcript_rows(
        rows,
        config,
        sample_remaining=config.classification.sample_limit,
    )
    stats = _TranscriptRunStats(
        transcripts_in_window=len(rows),
        transcripts_with_text=sum(1 for r in rows if (r.transcript_text or "").strip()),
    )
    segments, classified_new, errors, persisted = _classify_and_persist_batch(
        session,
        filtered,
        config=config,
        settings=settings,
        api_key=api_key,
        reanalyze=reanalyze,
        progress=progress,
    )
    stats.classified_new = classified_new
    stats.classify_errors = errors
    stats.persisted = persisted

    if stats.persisted:
        ensure_analytics_views(get_engine(settings.database_url))

    return _assemble_report(
        session,
        segments=segments,
        stats=stats,
        time_window=time_window,
        config=config,
        settings=settings,
        api_key=api_key,
        use_reduction_llm=use_reduction_llm,
        batch_size=batch_size,
    )


def _assemble_report(
    session: Session,
    *,
    segments: list[_ClassifiedSegment],
    stats: _TranscriptRunStats,
    time_window: TimeWindow,
    config: TranscriptSummaryConfig,
    settings: Settings,
    api_key: str,
    use_reduction_llm: bool | None,
    batch_size: int | None,
    chunk_days: int | None = None,
    commit_every: int | None = None,
) -> TranscriptSummaryReport:
    generated_at = datetime.now(timezone.utc).isoformat()
    total = len(segments)
    buckets = _aggregate_primary_buckets(segments, total=total, config=config)

    reduction_requested = (
        use_reduction_llm
        if use_reduction_llm is not None
        else config.reduction_recommendations.enabled
    )
    llm_meta: dict[str, Any] = {
        "classification_model": settings.openai_model,
        "segments_classified_this_run": stats.classified_new,
        "classification_errors": stats.classify_errors,
        "reduction_llm_requested": reduction_requested,
        "reduction_llm_applied": False,
        "reduction_reasons_processed": 0,
    }

    if reduction_requested and api_key:
        sample_ids: set[str] = set()
        segments_by_primary: dict[str, list[_ClassifiedSegment]] = defaultdict(list)
        for item in segments:
            primary_key, _, _ = reason_keys(item.analysis)
            segments_by_primary[primary_key].append(item)
        for bucket in buckets[: config.reduction_recommendations.top_primary_reasons]:
            primary_segments = segments_by_primary.get(bucket.primary_key, [])
            ranked_ids = _rank_segment_ids_for_sampling(primary_segments)
            for segment_id in ranked_ids[: config.reduction_recommendations.samples_per_primary]:
                sample_ids.add(segment_id)

        transcript_by_segment = _fetch_transcript_texts(session, sample_ids)
        analysis_by_segment = {s.segment_id: s.analysis for s in segments}
        _enrich_reduction_recommendations(
            buckets,
            segments,
            transcript_by_segment=transcript_by_segment,
            analysis_by_segment=analysis_by_segment,
            settings=settings,
            api_key=api_key,
            config=config,
            llm_meta=llm_meta,
        )

    _apply_rule_recommendations(buckets)

    totals = {
        "transcripts_in_window": stats.transcripts_in_window,
        "transcripts_with_text": stats.transcripts_with_text,
        "transcripts_analyzed": total,
        "transcripts_classified_this_run": stats.classified_new,
        "transcripts_persisted_this_run": stats.persisted,
        "transcripts_reused_from_cache": total - stats.classified_new,
        "classification_error_count": stats.classify_errors,
        "summaries_in_database": total if config.classification.store_results else 0,
    }

    insights = _build_insights(
        rows_considered=stats.transcripts_in_window,
        total=total,
        buckets=buckets,
        call_selection=config.call_selection,
        classify_errors=stats.classify_errors,
    )

    classification_meta: dict[str, Any] = {
        "max_transcript_chars": config.classification.max_transcript_chars,
        "concurrency": config.classification.concurrency,
        "store_results": config.classification.store_results,
        "skip_existing": config.classification.skip_existing,
        "sample_limit": config.classification.sample_limit,
    }
    if batch_size is not None:
        classification_meta["batch_size"] = batch_size
    if chunk_days is not None:
        classification_meta["chunk_days"] = chunk_days
    if commit_every is not None:
        classification_meta["commit_every"] = commit_every

    return TranscriptSummaryReport(
        generated_at=generated_at,
        timeframe={
            "preset": time_window.preset,
            "start": time_window.start.isoformat() if time_window.start else None,
            "end": time_window.end.isoformat() if time_window.end else None,
            "label": time_window.label,
        },
        filters=config.call_selection.to_dict(),
        totals=totals,
        classification=classification_meta,
        top_primary_reasons=buckets,
        insights=insights,
        llm=llm_meta,
    )


def _analysis_from_db_row(row: CxoneTranscriptAnalysisRow) -> TranscriptReasonAnalysis:
    return TranscriptReasonAnalysis(
        transcript_summary=row.transcript_summary,
        primary_reason=row.primary_reason,
        secondary_reason=row.secondary_reason,
        tertiary_reason=row.tertiary_reason,
        reduction_hint=row.reduction_hint,
    )


def _load_cached_analyses(
    session: Session,
    segment_ids: set[str],
) -> dict[str, TranscriptReasonAnalysis]:
    """Load cached LLM rows in batches (avoids huge IN (...) parameter lists)."""
    if not segment_ids:
        return {}

    result: dict[str, TranscriptReasonAnalysis] = {}
    ids = list(segment_ids)
    for offset in range(0, len(ids), _CACHE_LOOKUP_BATCH_SIZE):
        batch = ids[offset : offset + _CACHE_LOOKUP_BATCH_SIZE]
        stmt = select(CxoneTranscriptAnalysisRow).where(
            CxoneTranscriptAnalysisRow.segment_id.in_(batch)
        )
        for row in session.scalars(stmt).all():
            result[row.segment_id] = _analysis_from_db_row(row)
    return result


def _classify_segments(
    rows: list[CxoneTranscriptRow],
    *,
    settings: Settings,
    api_key: str,
    config: TranscriptSummaryConfig,
) -> tuple[dict[str, TranscriptReasonAnalysis], int]:
    if not rows:
        return {}, 0

    results: dict[str, TranscriptReasonAnalysis] = {}
    errors = 0
    concurrency = max(1, config.classification.concurrency)

    def classify_one(row: CxoneTranscriptRow) -> tuple[str, TranscriptReasonAnalysis | None]:
        try:
            analysis = classify_transcript(
                transcript_text=row.transcript_text or "",
                segment_summary=row.segment_summary,
                client_sentiment=row.client_sentiment,
                skill_name=row.skill_name,
                agent_name=row.agent_name,
                api_key=api_key,
                model=settings.openai_model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.request_timeout_seconds,
                max_transcript_chars=config.classification.max_transcript_chars,
            )
            return row.segment_id, analysis
        except (TranscriptClassificationError, Exception) as exc:
            logger.warning("Classification failed for %s: %s", row.segment_id, exc)
            return row.segment_id, None

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = [executor.submit(classify_one, row) for row in rows]
        for future in as_completed(futures):
            segment_id, analysis = future.result()
            if analysis is None:
                errors += 1
                continue
            results[segment_id] = analysis

    return results, errors


def _persist_analyses(
    session: Session,
    analyses: dict[str, TranscriptReasonAnalysis],
    *,
    model: str,
) -> int:
    now = datetime.now(timezone.utc)
    for segment_id, analysis in analyses.items():
        row = session.get(CxoneTranscriptAnalysisRow, segment_id)
        if row is None:
            row = CxoneTranscriptAnalysisRow(
                segment_id=segment_id,
                analyzed_at=now,
                model=model,
            )
            session.add(row)
        row.transcript_summary = analysis.transcript_summary
        row.primary_reason = analysis.primary_reason
        row.secondary_reason = analysis.secondary_reason
        row.tertiary_reason = analysis.tertiary_reason
        row.reduction_hint = analysis.reduction_hint
        row.model = model
        row.analyzed_at = now
    return len(analyses)


def _aggregate_primary_buckets(
    segments: list[_ClassifiedSegment],
    *,
    total: int,
    config: TranscriptSummaryConfig,
) -> list[PrimaryReasonBucket]:
    by_primary: dict[str, list[_ClassifiedSegment]] = defaultdict(list)
    display_primary: dict[str, str] = {}

    for item in segments:
        primary_key, _, _ = reason_keys(item.analysis)
        by_primary[primary_key].append(item)
        current = display_primary.get(primary_key, "")
        if len(item.analysis.primary_reason) > len(current):
            display_primary[primary_key] = item.analysis.primary_reason

    buckets: list[PrimaryReasonBucket] = []
    for primary_key, items in by_primary.items():
        count = len(items)
        negative = sum(1 for i in items if is_negative_sentiment(i.client_sentiment))
        hints = list(
            dict.fromkeys(
                h
                for i in items
                if (h := (i.analysis.reduction_hint or "").strip())
            )
        )[:5]

        secondary_groups: dict[str, list[_ClassifiedSegment]] = defaultdict(list)
        secondary_display: dict[str, str] = {}
        for item in items:
            _, secondary_key, _ = reason_keys(item.analysis)
            secondary_groups[secondary_key].append(item)
            current = secondary_display.get(secondary_key, "")
            if len(item.analysis.secondary_reason) > len(current):
                secondary_display[secondary_key] = item.analysis.secondary_reason

        secondary_items: list[SecondaryBreakdownItem] = []
        for secondary_key, sec_items in secondary_groups.items():
            sec_count = len(sec_items)
            tertiary_counter: Counter[str] = Counter()
            tertiary_display: dict[str, str] = {}
            for item in sec_items:
                _, _, tertiary_key = reason_keys(item.analysis)
                if tertiary_key and item.analysis.tertiary_reason:
                    tertiary_counter[tertiary_key] += 1
                    current = tertiary_display.get(tertiary_key, "")
                    if len(item.analysis.tertiary_reason) > len(current):
                        tertiary_display[tertiary_key] = item.analysis.tertiary_reason

            tertiary_items = [
                TertiaryBreakdownItem(
                    tertiary_reason=tertiary_display.get(key, key),
                    count=sub_count,
                    share_of_secondary_pct=round(100.0 * sub_count / sec_count, 1)
                    if sec_count
                    else 0.0,
                )
                for key, sub_count in tertiary_counter.most_common(
                    config.report.top_tertiary_per_secondary
                )
            ]

            secondary_items.append(
                SecondaryBreakdownItem(
                    secondary_reason=secondary_display.get(
                        secondary_key, secondary_key
                    ),
                    count=sec_count,
                    share_of_primary_pct=round(100.0 * sec_count / count, 1) if count else 0.0,
                    tertiary=tertiary_items,
                )
            )

        secondary_items.sort(key=lambda s: (-s.count, s.secondary_reason))

        summaries: list[str] = []
        sample_ids: list[str] = []
        for item in items:
            if item.analysis.transcript_summary not in summaries:
                summaries.append(item.analysis.transcript_summary)
            if item.segment_id not in sample_ids:
                sample_ids.append(item.segment_id)

        bucket = PrimaryReasonBucket(
            primary_reason=display_primary.get(primary_key, primary_key),
            primary_key=primary_key,
            count=count,
            share_pct=round(100.0 * count / total, 1) if total else 0.0,
            importance_score=score_reason_bucket(
                count=count,
                total=total or 1,
                negative_sentiment_count=negative,
                high_priority_count=0,
            ),
            negative_sentiment_pct=round(100.0 * negative / count, 1) if count else 0.0,
            secondary=secondary_items[: config.report.top_secondary_per_primary],
            sample_summaries=summaries[:3],
            sample_segment_ids=sample_ids[:5],
            reduction_hints=hints,
        )
        buckets.append(bucket)

    buckets.sort(key=lambda b: (-b.count, -b.importance_score, b.primary_reason))
    return buckets[: config.report.top_primary_reasons]


def _enrich_reduction_recommendations(
    buckets: list[PrimaryReasonBucket],
    segments: list[_ClassifiedSegment],
    *,
    transcript_by_segment: dict[str, str],
    analysis_by_segment: dict[str, TranscriptReasonAnalysis],
    settings: Settings,
    api_key: str,
    config: TranscriptSummaryConfig,
    llm_meta: dict[str, Any],
) -> None:
    segments_by_primary: dict[str, list[_ClassifiedSegment]] = defaultdict(list)
    for item in segments:
        primary_key, _, _ = reason_keys(item.analysis)
        segments_by_primary[primary_key].append(item)

    for bucket in buckets[: config.reduction_recommendations.top_primary_reasons]:
        primary_segments = segments_by_primary.get(bucket.primary_key, [])
        ranked_ids = _rank_segment_ids_for_sampling(primary_segments)
        samples = samples_from_analyses(
            segment_ids=ranked_ids,
            transcript_by_segment=transcript_by_segment,
            analysis_by_segment=analysis_by_segment,
            max_samples=config.reduction_recommendations.samples_per_primary,
            max_transcript_chars=config.reduction_recommendations.max_transcript_chars,
        )
        if not samples:
            continue

        secondary_breakdown = [
            (s.secondary_reason, s.count, s.share_of_primary_pct) for s in bucket.secondary
        ]
        try:
            recs = generate_primary_reason_reductions(
                primary_reason=bucket.primary_reason,
                count=bucket.count,
                share_pct=bucket.share_pct,
                secondary_breakdown=secondary_breakdown,
                samples=samples,
                api_key=api_key,
                model=settings.openai_model,
                base_url=settings.openai_base_url,
                timeout_seconds=settings.request_timeout_seconds,
            )
        except (TranscriptReductionError, Exception) as exc:
            logger.warning(
                "Reduction recommendations failed for %s: %s",
                bucket.primary_reason,
                exc,
            )
            continue

        if recs:
            bucket.recommendations = recs
            bucket.recommendation_source = "llm"
            llm_meta["reduction_llm_applied"] = True
            llm_meta["reduction_reasons_processed"] = (
                int(llm_meta.get("reduction_reasons_processed", 0)) + 1
            )


def _apply_rule_recommendations(buckets: list[PrimaryReasonBucket]) -> None:
    for bucket in buckets:
        if bucket.recommendation_source == "llm" and bucket.recommendations:
            continue
        bucket.recommendations = recommendations_for_reason(bucket.primary_reason)
        bucket.recommendation_source = "rules"


def _rank_segment_ids_for_sampling(segments: list[_ClassifiedSegment]) -> list[str]:
    def sort_key(item: _ClassifiedSegment) -> tuple[int, str]:
        negative = int(is_negative_sentiment(item.client_sentiment))
        return (-negative, item.segment_id)

    return [item.segment_id for item in sorted(segments, key=sort_key)]


def _build_insights(
    *,
    rows_considered: int,
    total: int,
    buckets: list[PrimaryReasonBucket],
    call_selection,
    classify_errors: int,
) -> list[str]:
    insights: list[str] = []
    if rows_considered and total < rows_considered:
        summary = exclusion_summary(call_selection)
        insights.append(
            f"{rows_considered - total} segment(s) excluded by filters or empty transcript "
            f"({summary}); {total} classified."
        )
    if classify_errors:
        insights.append(
            f"{classify_errors} transcript(s) failed LLM classification — re-run with "
            "--reanalyze or check API limits."
        )
    if buckets:
        top = buckets[0]
        insights.append(
            f'Top transcript-derived reason: "{top.primary_reason}" ({top.count} calls, '
            f"{top.share_pct}% of volume, importance {top.importance_score}/100)."
        )
        if top.secondary:
            sub = top.secondary[0]
            insights.append(
                f'Within "{top.primary_reason}", most common sub-reason: '
                f'"{sub.secondary_reason}" ({sub.count} calls, {sub.share_of_primary_pct}% of that bucket).'
            )
        if len(buckets) >= 3:
            top_three = sum(b.share_pct for b in buckets[:3])
            insights.append(
                f"Top 3 primary reasons account for {top_three:.1f}% of classified calls — "
                "target deflection and process fixes there first."
            )
    return insights


def format_report_text(report: TranscriptSummaryReport) -> str:
    lines: list[str] = []
    lines.append("Transcript summary (cxone_transcripts)")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.generated_at}")
    lines.append(f"Period:    {report.timeframe.get('label', 'n/a')}")
    if report.timeframe.get("start"):
        lines.append(f"           {report.timeframe['start']} -> {report.timeframe['end']}")
    lines.append("")

    if report.filters:
        lines.append("Call selection")
        lines.append("-" * 72)
        for key, value in report.filters.items():
            if key in ("link_methods", "include_unmatched"):
                continue
            if isinstance(value, list) and not value:
                continue
            lines.append(f"  {key}: {value}")
        lines.append("")

    lines.append("Totals")
    lines.append("-" * 72)
    for key, value in report.totals.items():
        label = key.replace("_", " ").strip().capitalize()
        lines.append(f"  {label}: {value}")
    lines.append("")

    if report.insights:
        lines.append("Key insights")
        lines.append("-" * 72)
        for insight in report.insights:
            lines.append(f"  - {insight}")
        lines.append("")

    lines.append("Top primary call reasons (from transcripts)")
    lines.append("-" * 72)
    for index, bucket in enumerate(report.top_primary_reasons, start=1):
        lines.append(
            f"{index:2}. {bucket.primary_reason} - {bucket.count} calls "
            f"({bucket.share_pct}%), importance {bucket.importance_score}/100"
        )
        if bucket.negative_sentiment_pct:
            lines.append(f"     Negative sentiment: {bucket.negative_sentiment_pct}%")
        if bucket.secondary:
            lines.append("     Secondary breakdown:")
            for sub in bucket.secondary:
                line = (
                    f"       - {sub.secondary_reason}: {sub.count} "
                    f"({sub.share_of_primary_pct}% of primary)"
                )
                lines.append(line)
                for ter in sub.tertiary[:3]:
                    lines.append(
                        f"           · {ter.tertiary_reason}: {ter.count} "
                        f"({ter.share_of_secondary_pct}% of secondary)"
                    )
        if bucket.reduction_hints:
            lines.append("     Per-call reduction hints (sample):")
            for hint in bucket.reduction_hints[:2]:
                lines.append(f"       - {hint}")
        source = bucket.recommendation_source
        lines.append(f"     Recommendations to reduce volume ({source}):")
        for rec in bucket.recommendations:
            lines.append(f"       - {rec}")
        lines.append("")

    if report.llm:
        lines.append("LLM metadata")
        lines.append("-" * 72)
        for key, value in report.llm.items():
            lines.append(f"  {key}: {value}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report_json(report: TranscriptSummaryReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def write_report_markdown(report: TranscriptSummaryReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Transcript summary (cxone_transcripts)",
        "",
        f"- **Generated:** {report.generated_at}",
        f"- **Period:** {report.timeframe.get('label', 'n/a')}",
        "",
        "## Totals",
        "",
    ]
    for key, value in report.totals.items():
        lines.append(f"- **{key.replace('_', ' ')}:** {value}")
    lines.extend(["", "## Key insights", ""])
    for insight in report.insights:
        lines.append(f"- {insight}")
    lines.extend(["", "## Top primary reasons", ""])
    for index, bucket in enumerate(report.top_primary_reasons, start=1):
        lines.append(
            f"### {index}. {bucket.primary_reason} "
            f"({bucket.count} calls, {bucket.share_pct}%)"
        )
        lines.append("")
        if bucket.secondary:
            lines.append("**Secondary breakdown:**")
            for sub in bucket.secondary:
                lines.append(
                    f"- {sub.secondary_reason}: {sub.count} ({sub.share_of_primary_pct}%)"
                )
                for ter in sub.tertiary:
                    lines.append(
                        f"  - {ter.tertiary_reason}: {ter.count} "
                        f"({ter.share_of_secondary_pct}%)"
                    )
            lines.append("")
        lines.append(f"**Recommendations ({bucket.recommendation_source}):**")
        for rec in bucket.recommendations:
            lines.append(f"- {rec}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")
