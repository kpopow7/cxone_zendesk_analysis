from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from orchestration.analysis.call_selection import (
    CallSelectionOverrides,
    exclusion_summary,
    row_matches_call_selection,
)
from orchestration.analysis.config import SummaryAnalysisConfig, load_summary_config
from orchestration.analysis.disposition_labels import load_disposition_label_config
from orchestration.analysis.importance import build_reason_buckets
from orchestration.analysis.llm_enrichment import (
    apply_rule_recommendations,
    enrich_buckets_with_llm_recommendations,
)
from orchestration.analysis.reasons import (
    InteractionSlice,
    extract_call_reason,
    extract_disposition,
    normalize_reason_key,
)
from orchestration.analysis.timeframes import TimeWindow, resolve_time_window
from orchestration.config import Settings
from orchestration.db.schema import CombinedInteractionRow
from orchestration.db.session import get_session_factory


@dataclass
class InteractionSummaryReport:
    generated_at: str
    timeframe: dict[str, Any]
    filters: dict[str, Any]
    totals: dict[str, int | float]
    link_methods: list[dict[str, int | str]]
    top_dispositions: list[dict[str, Any]]
    top_call_reasons: list[dict[str, Any]]
    insights: list[str] = field(default_factory=list)
    llm: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def run_interaction_summary(
    settings: Settings,
    *,
    time_window: TimeWindow,
    analysis_config: SummaryAnalysisConfig | None = None,
    config_path: Path | None = None,
    use_llm_recommendations: bool | None = None,
    selection_overrides: CallSelectionOverrides | None = None,
) -> InteractionSummaryReport:
    if analysis_config is None:
        path = config_path or Path(settings.interaction_summary_config_path)
        config = load_summary_config(path, selection_overrides=selection_overrides)
    else:
        config = analysis_config
        if selection_overrides:
            from dataclasses import replace

            from orchestration.analysis.call_selection import apply_call_selection_overrides

            config = replace(
                config,
                call_selection=apply_call_selection_overrides(
                    config.call_selection,
                    selection_overrides,
                ),
            )

    session_factory = get_session_factory(settings.database_url)
    with session_factory() as session:
        rows = _fetch_interactions(session, time_window, config)
        return _build_report(
            rows,
            time_window=time_window,
            config=config,
            settings=settings,
            use_llm_recommendations=use_llm_recommendations,
        )


def _fetch_interactions(
    session: Session,
    time_window: TimeWindow,
    config: SummaryAnalysisConfig,
) -> list[CombinedInteractionRow]:
    stmt = select(CombinedInteractionRow)
    if time_window.start is not None:
        stmt = stmt.where(CombinedInteractionRow.interaction_start >= time_window.start)
    if time_window.end is not None:
        stmt = stmt.where(CombinedInteractionRow.interaction_start <= time_window.end)
    return list(session.scalars(stmt).all())


def _build_report(
    rows: list[CombinedInteractionRow],
    *,
    time_window: TimeWindow,
    config: SummaryAnalysisConfig,
    settings: Settings,
    use_llm_recommendations: bool | None = None,
) -> InteractionSummaryReport:
    generated_at = datetime.now(timezone.utc).isoformat()
    label_config = load_disposition_label_config(
        Path(config.disposition_label_map_path)
    )
    disposition_labels = label_config.labels
    disposition_fallback = (
        config.disposition_fallback_humanize and label_config.fallback_humanize
    )

    filtered: list[CombinedInteractionRow] = []
    link_method_counts: Counter[str] = Counter()

    for row in rows:
        link_method_counts[row.link_method or "unknown"] += 1
        if not row_matches_call_selection(row, config.call_selection):
            continue
        filtered.append(row)

    slices: list[InteractionSlice] = []
    disposition_label_counts: Counter[str] = Counter()
    disposition_codes_by_label: dict[str, Counter[str]] = defaultdict(Counter)
    with_reason = 0

    for row in filtered:
        promoted = row.zendesk_promoted_fields if isinstance(row.zendesk_promoted_fields, dict) else {}
        call_reason, source = _resolve_call_reason(row, promoted, config)
        if source is not None:
            with_reason += 1

        disposition_code, disposition_label = _resolve_disposition(
            row,
            promoted,
            config,
            disposition_labels,
            disposition_fallback,
        )
        if disposition_label:
            disposition_label_counts[disposition_label] += 1
            if disposition_code:
                disposition_codes_by_label[disposition_label][disposition_code] += 1

        slices.append(
            InteractionSlice(
                segment_id=row.segment_id,
                interaction_start=row.interaction_start,
                link_method=row.link_method or "unknown",
                call_reason=call_reason,
                call_reason_key=normalize_reason_key(call_reason),
                call_reason_source=source,
                disposition_code=disposition_code,
                disposition_label=disposition_label,
                transcript_text=row.transcript_text or "",
                ticket_subject=row.ticket_subject,
                segment_summary=row.segment_summary,
                client_sentiment=row.client_sentiment,
                ticket_priority=row.ticket_priority,
                skill_name=row.skill_name,
                team_name=row.team_name,
            )
        )

    total = len(slices)
    buckets = build_reason_buckets(slices, total_interactions=total or 1, top_n=config.top_n)
    slices_by_reason_key: dict[str, list[InteractionSlice]] = {}
    for item in slices:
        slices_by_reason_key.setdefault(item.call_reason_key, []).append(item)

    llm_requested = (
        use_llm_recommendations
        if use_llm_recommendations is not None
        else config.llm.enabled
    )
    llm_meta = enrich_buckets_with_llm_recommendations(
        buckets,
        slices_by_reason_key,
        settings=settings,
        config=config,
        use_llm=llm_requested,
    )
    apply_rule_recommendations(buckets)

    top_reasons_payload = [_bucket_to_dict(bucket) for bucket in buckets]
    top_dispositions = _build_top_dispositions(
        disposition_label_counts=disposition_label_counts,
        disposition_codes_by_label=disposition_codes_by_label,
        total=total,
        top_n=config.top_n,
    )

    totals = {
        "interactions_in_window": len(rows),
        "interactions_analyzed": total,
        "with_call_reason_field": with_reason,
        "call_reason_capture_pct": round(100.0 * with_reason / total, 1) if total else 0.0,
    }

    insights = _build_insights(
        total=total,
        buckets=buckets,
        totals=totals,
        link_method_counts=link_method_counts,
        rows_considered=len(rows),
        call_selection=config.call_selection,
    )

    return InteractionSummaryReport(
        generated_at=generated_at,
        timeframe={
            "preset": time_window.preset,
            "start": time_window.start.isoformat() if time_window.start else None,
            "end": time_window.end.isoformat() if time_window.end else None,
            "label": time_window.label,
        },
        filters=config.call_selection.to_dict(),
        totals=totals,
        link_methods=[
            {
                "link_method": method,
                "count": count,
                "share_pct": round(100.0 * count / len(rows), 1) if rows else 0.0,
            }
            for method, count in link_method_counts.most_common()
        ],
        top_dispositions=top_dispositions,
        top_call_reasons=top_reasons_payload,
        insights=insights,
        llm=llm_meta,
    )


def _resolve_call_reason(
    row: CombinedInteractionRow,
    promoted: dict,
    config: SummaryAnalysisConfig,
) -> tuple[str, str | None]:
    if row.call_reason and str(row.call_reason).strip():
        return str(row.call_reason).strip(), row.call_reason_source
    return extract_call_reason(
        promoted,
        reason_fields=config.call_reason_fields,
        ticket_subject=row.ticket_subject,
        fallback_to_ticket_subject=config.fallback_to_ticket_subject,
        humanize_codes=config.humanize_reason_codes,
    )


def _resolve_disposition(
    row: CombinedInteractionRow,
    promoted: dict,
    config: SummaryAnalysisConfig,
    label_map: dict[str, str],
    fallback_humanize: bool,
) -> tuple[str | None, str | None]:
    if row.disposition_label and str(row.disposition_label).strip():
        code = row.disposition_code.strip() if row.disposition_code else None
        return code, str(row.disposition_label).strip()
    return extract_disposition(
        promoted,
        disposition_fields=config.disposition_fields,
        label_map=label_map,
        fallback_humanize=fallback_humanize,
    )


def _build_top_dispositions(
    *,
    disposition_label_counts: Counter[str],
    disposition_codes_by_label: dict[str, Counter[str]],
    total: int,
    top_n: int,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for label, count in disposition_label_counts.most_common(top_n):
        code_counter = disposition_codes_by_label.get(label)
        top_code = code_counter.most_common(1)[0][0] if code_counter else None
        results.append(
            {
                "disposition": label,
                "disposition_code": top_code,
                "count": count,
                "share_pct": round(100.0 * count / total, 1) if total else 0.0,
            }
        )
    return results


def _bucket_to_dict(bucket) -> dict[str, Any]:
    return {
        "reason": bucket.reason,
        "count": bucket.count,
        "share_pct": bucket.share_pct,
        "importance_score": bucket.importance_score,
        "negative_sentiment_pct": bucket.negative_sentiment_pct,
        "high_priority_pct": bucket.high_priority_pct,
        "source_fields": dict(bucket.source_field_counts),
        "top_skills": [{"skill": name, "count": count} for name, count in bucket.top_skills],
        "sample_subjects": bucket.sample_subjects,
        "sample_summaries": bucket.sample_summaries,
        "sample_segment_ids": bucket.sample_segment_ids[:5],
        "recommendations": bucket.recommendations,
        "recommendation_source": bucket.recommendation_source,
    }


def _build_insights(
    *,
    total: int,
    buckets: list,
    totals: dict[str, int | float],
    link_method_counts: Counter[str],
    rows_considered: int,
    call_selection,
) -> list[str]:
    insights: list[str] = []
    if rows_considered and total < rows_considered:
        summary = exclusion_summary(call_selection)
        insights.append(
            f"{rows_considered - total} interaction(s) excluded by call selection "
            f"({summary}); {total} included in reason ranking."
        )

    capture = float(totals.get("call_reason_capture_pct", 0))
    if total and capture < 70:
        insights.append(
            f"Only {capture:.0f}% of analyzed calls have a structured reason field - "
            "improve ticket form enforcement or linking before relying on rankings."
        )

    if buckets:
        top = buckets[0]
        insights.append(
            f'Highest-impact reason: "{top.reason}" ({top.count} calls, '
            f"importance {top.importance_score}/100, {top.share_pct}% of volume)."
        )
        if len(buckets) >= 3:
            top_three_share = sum(b.share_pct for b in buckets[:3])
            insights.append(
                f"Top 3 reasons account for {top_three_share:.1f}% of analyzed call volume - "
                "prioritize deflection and process fixes there first."
            )

    unmatched = link_method_counts.get("unmatched", 0)
    if rows_considered and unmatched:
        pct = round(100.0 * unmatched / rows_considered, 1)
        if pct >= 5:
            insights.append(
                f"{unmatched} segment(s) ({pct}%) are unmatched to Zendesk - "
                "re-run combined dataset build after ticket extracts."
            )

    return insights


def format_report_text(report: InteractionSummaryReport) -> str:
    lines: list[str] = []
    lines.append("Interaction summary")
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

    lines.append(f"Top call reasons (by volume and importance, top {len(report.top_call_reasons)})")
    lines.append("-" * 72)
    for index, item in enumerate(report.top_call_reasons, start=1):
        lines.append(
            f"{index:2}. {item['reason']} - {item['count']} calls "
            f"({item['share_pct']}%), importance {item['importance_score']}/100"
        )
        if item.get("negative_sentiment_pct"):
            lines.append(
                f"     Negative sentiment: {item['negative_sentiment_pct']}%  |  "
                f"High/urgent priority: {item.get('high_priority_pct', 0)}%"
            )
        if item.get("top_skills"):
            skills = ", ".join(f"{s['skill']} ({s['count']})" for s in item["top_skills"][:3])
            lines.append(f"     Top skills: {skills}")
        source = item.get("recommendation_source", "rules")
        lines.append(f"     Recommendations ({source}):")
        for rec in item.get("recommendations", []):
            lines.append(f"       - {rec}")
        lines.append("")

    if report.top_dispositions:
        lines.append("Top dispositions")
        lines.append("-" * 72)
        for index, item in enumerate(report.top_dispositions[:10], start=1):
            code = item.get("disposition_code")
            code_suffix = f" [{code}]" if code else ""
            lines.append(
                f"{index:2}. {item['disposition']}{code_suffix} - "
                f"{item['count']} ({item['share_pct']}%)"
            )
        lines.append("")

    if report.llm:
        lines.append("LLM recommendations")
        lines.append("-" * 72)
        for key, value in report.llm.items():
            lines.append(f"  {key}: {value}")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_report_json(report: InteractionSummaryReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")


def write_report_markdown(report: InteractionSummaryReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Interaction summary",
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
    lines.extend(["", "## Top call reasons", ""])
    for index, item in enumerate(report.top_call_reasons, start=1):
        lines.append(
            f"### {index}. {item['reason']} "
            f"({item['count']} calls, {item['share_pct']}%, "
            f"importance {item['importance_score']}/100)"
        )
        lines.append("")
        source = item.get("recommendation_source", "rules")
        lines.append(f"**Recommendations ({source}):**")
        for rec in item.get("recommendations", []):
            lines.append(f"- {rec}")
        lines.append("")
    if report.top_dispositions:
        lines.extend(["", "## Top dispositions", ""])
        for index, item in enumerate(report.top_dispositions, start=1):
            code = item.get("disposition_code")
            code_note = f" (`{code}`)" if code else ""
            lines.append(
                f"{index}. {item['disposition']}{code_note} - "
                f"{item['count']} ({item['share_pct']}%)"
            )
    path.write_text("\n".join(lines), encoding="utf-8")
