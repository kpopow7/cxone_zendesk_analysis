from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestration.analysis.call_selection import (
    CallSelectionOverrides,
    exclusion_summary,
    row_matches_segment_filters,
)
from orchestration.analysis.importance import score_reason_bucket
from orchestration.analysis.reasons import is_negative_sentiment, normalize_reason_key
from orchestration.analysis.recommendations import recommendations_for_reason
from orchestration.analysis.timeframes import TimeWindow
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
from orchestration.db.schema import (
    CxoneTranscriptAnalysisRow,
    CxoneTranscriptRow,
    ensure_transcript_analysis_table,
)
from orchestration.db.session import get_session_factory

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

    ensure_transcript_analysis_table(settings.database_url)

    session_factory = get_session_factory(settings.database_url)
    with session_factory() as session:
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
        )


def _fetch_transcripts(
    session: Session,
    time_window: TimeWindow,
) -> list[CxoneTranscriptRow]:
    stmt = select(CxoneTranscriptRow)
    if time_window.start is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CxoneTranscriptRow.interaction_start <= time_window.end)
    return list(session.scalars(stmt).all())


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
) -> TranscriptSummaryReport:
    generated_at = datetime.now(timezone.utc).isoformat()
    filtered: list[CxoneTranscriptRow] = []
    for row in rows:
        if not row_matches_segment_filters(row, config.call_selection):
            continue
        if not (row.transcript_text or "").strip():
            continue
        filtered.append(row)

    if config.classification.sample_limit is not None:
        filtered = filtered[: config.classification.sample_limit]

    existing = _load_cached_analyses(
        session,
        {row.segment_id for row in filtered},
    )
    skip_existing = config.classification.skip_existing and not reanalyze

    to_classify: list[CxoneTranscriptRow] = []
    for row in filtered:
        if skip_existing and row.segment_id in existing:
            continue
        to_classify.append(row)

    classified_new, classify_errors = _classify_segments(
        to_classify,
        settings=settings,
        api_key=api_key,
        config=config,
    )

    if config.classification.store_results and classified_new:
        _persist_analyses(
            session,
            classified_new,
            model=settings.openai_model,
        )
        session.commit()

    analyses: dict[str, TranscriptReasonAnalysis] = {}
    for segment_id, row in existing.items():
        analyses[segment_id] = row
    analyses.update(classified_new)

    segments: list[_ClassifiedSegment] = []
    for row in filtered:
        analysis = analyses.get(row.segment_id)
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

    total = len(segments)
    buckets = _aggregate_primary_buckets(segments, total=total, config=config)

    reduction_requested = (
        use_reduction_llm
        if use_reduction_llm is not None
        else config.reduction_recommendations.enabled
    )
    llm_meta: dict[str, Any] = {
        "classification_model": settings.openai_model,
        "segments_classified_this_run": len(classified_new),
        "classification_errors": classify_errors,
        "reduction_llm_requested": reduction_requested,
        "reduction_llm_applied": False,
        "reduction_reasons_processed": 0,
    }

    if reduction_requested and api_key:
        transcript_by_segment = {
            row.segment_id: row.transcript_text or ""
            for row in filtered
        }
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
        "transcripts_in_window": len(rows),
        "transcripts_with_text": sum(1 for r in rows if (r.transcript_text or "").strip()),
        "transcripts_analyzed": total,
        "transcripts_classified_this_run": len(classified_new),
        "transcripts_reused_from_cache": total - len(classified_new),
        "classification_error_count": classify_errors,
    }

    insights = _build_insights(
        rows_considered=len(rows),
        total=total,
        buckets=buckets,
        call_selection=config.call_selection,
        classify_errors=classify_errors,
    )

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
        classification={
            "max_transcript_chars": config.classification.max_transcript_chars,
            "concurrency": config.classification.concurrency,
            "store_results": config.classification.store_results,
            "skip_existing": config.classification.skip_existing,
            "sample_limit": config.classification.sample_limit,
        },
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
) -> None:
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
