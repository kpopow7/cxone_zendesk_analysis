"""Tagging-accuracy QA (P2).

Validates the reason data the rest of the analysis rests on by comparing what the agent
tagged on the Zendesk ticket (call_reason -> canonical) against what the call transcript was
actually about (primary_reason -> canonical). Both are mapped through the controlled taxonomy
so the comparison is apples-to-apples.

The aggregation (:func:`build_tagging_qa_report`) is a pure function over already-fetched
rows so it is easy to unit-test; :func:`run_tagging_qa` runs the SQL against the analytics
views and feeds it in.

A row where either side resolved to the taxonomy fallback ("Other / Uncategorized") is treated
as a *taxonomy gap* (the vocabulary could not place the reason), not a confident agent mis-tag.
The headline accuracy is reported both ways so a taxonomy gap does not masquerade as a tagging
problem.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import text

from orchestration.analysis.reason_taxonomy import (
    DEFAULT_FALLBACK_CANONICAL as FALLBACK_CANONICAL,
)
from orchestration.config import Settings
from orchestration.db.analytics_views import ensure_analytics_views
from orchestration.db.session import get_engine


@dataclass(frozen=True)
class ReasonAccuracyRow:
    """Per-tagged-reason accuracy from the comparable population."""

    tagged_reason_canonical: str
    comparable: int
    agree: int
    disagree: int

    @property
    def agree_pct(self) -> float:
        return round(100.0 * self.agree / self.comparable, 1) if self.comparable else 0.0

    @property
    def disagree_pct(self) -> float:
        return round(100.0 * self.disagree / self.comparable, 1) if self.comparable else 0.0


@dataclass(frozen=True)
class MismatchPair:
    tagged_reason_canonical: str
    transcript_reason_canonical: str
    count: int


@dataclass(frozen=True)
class MismatchSample:
    segment_id: str
    ticket_id: int | None
    interaction_start: str | None
    skill_name: str | None
    tagged_reason_canonical: str | None
    transcript_reason_canonical: str | None
    ticket_status: str | None


@dataclass
class TaggingQaReport:
    generated_at: str
    timeframe: dict[str, Any]
    min_volume: int
    # Comparable = both the tagged reason and the transcript reason mapped to a canonical label.
    comparable_calls: int
    agree_count: int
    disagree_count: int
    # Confident = comparable AND neither side fell to the taxonomy fallback.
    confident_comparable_calls: int
    confident_agree_count: int
    confident_disagree_count: int
    taxonomy_gap_calls: int
    worst_tagged_reasons: list[ReasonAccuracyRow] = field(default_factory=list)
    top_mismatch_pairs: list[MismatchPair] = field(default_factory=list)
    sample_mismatches: list[MismatchSample] = field(default_factory=list)

    @property
    def agree_pct(self) -> float:
        return round(100.0 * self.agree_count / self.comparable_calls, 1) if self.comparable_calls else 0.0

    @property
    def disagree_pct(self) -> float:
        return round(100.0 * self.disagree_count / self.comparable_calls, 1) if self.comparable_calls else 0.0

    @property
    def confident_agree_pct(self) -> float:
        return (
            round(100.0 * self.confident_agree_count / self.confident_comparable_calls, 1)
            if self.confident_comparable_calls
            else 0.0
        )

    @property
    def confident_disagree_pct(self) -> float:
        return (
            round(100.0 * self.confident_disagree_count / self.confident_comparable_calls, 1)
            if self.confident_comparable_calls
            else 0.0
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["agree_pct"] = self.agree_pct
        data["disagree_pct"] = self.disagree_pct
        data["confident_agree_pct"] = self.confident_agree_pct
        data["confident_disagree_pct"] = self.confident_disagree_pct
        # asdict already serialized nested dataclasses; re-attach derived per-reason pcts.
        for raw, row in zip(data["worst_tagged_reasons"], self.worst_tagged_reasons):
            raw["agree_pct"] = row.agree_pct
            raw["disagree_pct"] = row.disagree_pct
        return data


def build_tagging_qa_report(
    *,
    generated_at: str,
    timeframe: dict[str, Any],
    min_volume: int,
    per_reason_rows: list[ReasonAccuracyRow],
    mismatch_pairs: list[MismatchPair],
    sample_mismatches: list[MismatchSample],
    top_n: int = 15,
    fallback_label: str = FALLBACK_CANONICAL,
) -> TaggingQaReport:
    """Assemble a QA report from pre-aggregated rows. Pure (no DB access)."""
    comparable = sum(r.comparable for r in per_reason_rows)
    agree = sum(r.agree for r in per_reason_rows)
    disagree = sum(r.disagree for r in per_reason_rows)

    # Confident population excludes any reason that mapped to the taxonomy fallback.
    confident_rows = [r for r in per_reason_rows if r.tagged_reason_canonical != fallback_label]
    confident_comparable = sum(r.comparable for r in confident_rows)
    confident_agree = sum(r.agree for r in confident_rows)
    confident_disagree = sum(r.disagree for r in confident_rows)

    eligible = [
        r
        for r in per_reason_rows
        if r.comparable >= min_volume and r.tagged_reason_canonical != fallback_label
    ]
    worst = sorted(eligible, key=lambda r: (-r.disagree_pct, -r.comparable))[:top_n]

    return TaggingQaReport(
        generated_at=generated_at,
        timeframe=timeframe,
        min_volume=min_volume,
        comparable_calls=comparable,
        agree_count=agree,
        disagree_count=disagree,
        confident_comparable_calls=confident_comparable,
        confident_agree_count=confident_agree,
        confident_disagree_count=confident_disagree,
        taxonomy_gap_calls=comparable - confident_comparable,
        worst_tagged_reasons=worst,
        top_mismatch_pairs=mismatch_pairs[:top_n],
        sample_mismatches=sample_mismatches,
    )


def run_tagging_qa(
    settings: Settings,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    timeframe_label: str = "all time",
    min_volume: int = 20,
    top_n: int = 15,
    sample_limit: int = 25,
    ensure_views: bool = True,
) -> TaggingQaReport:
    """Query the analytics views and build a tagging-accuracy QA report."""
    engine = get_engine(settings.database_url)
    if ensure_views:
        ensure_analytics_views(engine)

    # Resolve the actual fallback label from the configured taxonomy (defaults to
    # "Other / Uncategorized") so "confident" filtering stays correct if it was customized.
    from orchestration.analysis.reason_taxonomy import load_reason_taxonomy

    fallback_label = load_reason_taxonomy("config/reason_taxonomy.json").fallback_canonical

    date_clause = ""
    params: dict[str, Any] = {}
    if start is not None:
        date_clause += " AND interaction_start >= :start"
        params["start"] = start
    if end is not None:
        date_clause += " AND interaction_start <= :end"
        params["end"] = end

    per_reason_sql = text(
        f"""
        SELECT call_reason_canonical AS tagged_reason_canonical,
               count(*) AS comparable,
               count(*) FILTER (WHERE reason_match_status = 'match') AS agree,
               count(*) FILTER (WHERE reason_match_status = 'mismatch') AS disagree
        FROM analytics_interaction_outcomes
        WHERE call_reason_canonical IS NOT NULL
          AND primary_reason_canonical IS NOT NULL
          {date_clause}
        GROUP BY call_reason_canonical
        """
    )

    pairs_sql = text(
        f"""
        SELECT tagged_reason_canonical, transcript_reason_canonical, count(*) AS n
        FROM analytics_reason_mismatches
        WHERE tagged_reason_canonical <> :fallback
          AND transcript_reason_canonical <> :fallback
          {date_clause}
        GROUP BY tagged_reason_canonical, transcript_reason_canonical
        ORDER BY n DESC
        LIMIT :top_n
        """
    )

    sample_sql = text(
        f"""
        SELECT segment_id, ticket_id, interaction_start, skill_name,
               tagged_reason_canonical, transcript_reason_canonical, ticket_status
        FROM analytics_reason_mismatches
        WHERE tagged_reason_canonical <> :fallback
          AND transcript_reason_canonical <> :fallback
          {date_clause}
        ORDER BY interaction_start DESC NULLS LAST
        LIMIT :sample_limit
        """
    )

    with engine.connect() as conn:
        per_reason_rows = [
            ReasonAccuracyRow(
                tagged_reason_canonical=row.tagged_reason_canonical,
                comparable=int(row.comparable),
                agree=int(row.agree),
                disagree=int(row.disagree),
            )
            for row in conn.execute(per_reason_sql, params)
        ]
        pair_params = {**params, "fallback": fallback_label, "top_n": top_n}
        mismatch_pairs = [
            MismatchPair(
                tagged_reason_canonical=row.tagged_reason_canonical,
                transcript_reason_canonical=row.transcript_reason_canonical,
                count=int(row.n),
            )
            for row in conn.execute(pairs_sql, pair_params)
        ]
        sample_params = {**params, "fallback": fallback_label, "sample_limit": sample_limit}
        sample_mismatches = [
            MismatchSample(
                segment_id=str(row.segment_id),
                ticket_id=int(row.ticket_id) if row.ticket_id is not None else None,
                interaction_start=row.interaction_start.isoformat()
                if row.interaction_start is not None
                else None,
                skill_name=row.skill_name,
                tagged_reason_canonical=row.tagged_reason_canonical,
                transcript_reason_canonical=row.transcript_reason_canonical,
                ticket_status=row.ticket_status,
            )
            for row in conn.execute(sample_sql, sample_params)
        ]

    return build_tagging_qa_report(
        generated_at=datetime.now().astimezone().isoformat(),
        timeframe={
            "label": timeframe_label,
            "start": start.isoformat() if start else None,
            "end": end.isoformat() if end else None,
        },
        min_volume=min_volume,
        per_reason_rows=per_reason_rows,
        mismatch_pairs=mismatch_pairs,
        sample_mismatches=sample_mismatches,
        top_n=top_n,
        fallback_label=fallback_label,
    )


def format_tagging_qa_text(report: TaggingQaReport) -> str:
    lines: list[str] = []
    lines.append("Tagging-accuracy QA (agent reason vs transcript reason)")
    lines.append("=" * 72)
    lines.append(f"Generated: {report.generated_at}")
    lines.append(f"Period:    {report.timeframe.get('label', 'n/a')}")
    lines.append("")

    lines.append("Overall agreement")
    lines.append("-" * 72)
    lines.append(
        f"  Comparable interactions (both reasons mapped): {report.comparable_calls}"
    )
    lines.append(
        f"  Agree: {report.agree_count} ({report.agree_pct}%)  |  "
        f"Disagree: {report.disagree_count} ({report.disagree_pct}%)"
    )
    lines.append("")
    lines.append(
        "  Confident (excludes taxonomy-fallback rows on either side):"
    )
    lines.append(
        f"    Comparable: {report.confident_comparable_calls}  |  "
        f"Agree: {report.confident_agree_count} ({report.confident_agree_pct}%)  |  "
        f"Disagree: {report.confident_disagree_count} ({report.confident_disagree_pct}%)"
    )
    lines.append(
        f"  Taxonomy-gap interactions (one side uncategorized): {report.taxonomy_gap_calls} "
        "- tune config/reason_taxonomy.json to shrink this."
    )
    lines.append("")

    lines.append(
        f"Worst-tagged reasons (min {report.min_volume} comparable, excl. uncategorized)"
    )
    lines.append("-" * 72)
    if report.worst_tagged_reasons:
        for row in report.worst_tagged_reasons:
            lines.append(
                f"  {row.tagged_reason_canonical}: {row.disagree_pct}% disagree "
                f"({row.disagree}/{row.comparable} comparable)"
            )
    else:
        lines.append("  (none above the volume threshold)")
    lines.append("")

    lines.append("Most common mis-tag pairs (tagged -> actually about)")
    lines.append("-" * 72)
    if report.top_mismatch_pairs:
        for pair in report.top_mismatch_pairs:
            lines.append(
                f"  {pair.tagged_reason_canonical} -> {pair.transcript_reason_canonical}: "
                f"{pair.count}"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    lines.append("Sample tickets to review")
    lines.append("-" * 72)
    if report.sample_mismatches:
        for s in report.sample_mismatches:
            ticket = f"ticket {s.ticket_id}" if s.ticket_id is not None else "no ticket"
            lines.append(
                f"  {s.segment_id} ({ticket}): tagged "
                f"'{s.tagged_reason_canonical}' but call was '{s.transcript_reason_canonical}'"
            )
    else:
        lines.append("  (none)")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def write_tagging_qa_json(report: TaggingQaReport, path) -> None:
    from pathlib import Path

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, default=str), encoding="utf-8")
