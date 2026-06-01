from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from orchestration.analysis.call_selection import (
    DEFAULT_CALL_SELECTION,
    CallSelectionFilters,
    CallSelectionOverrides,
    apply_call_selection_overrides,
)
from orchestration.analysis.config import _load_call_selection, resolve_summary_config_path


@dataclass(frozen=True)
class TranscriptClassificationConfig:
    max_transcript_chars: int
    concurrency: int
    store_results: bool
    skip_existing: bool
    sample_limit: int | None


@dataclass(frozen=True)
class TranscriptReportConfig:
    top_primary_reasons: int
    top_secondary_per_primary: int
    top_tertiary_per_secondary: int


@dataclass(frozen=True)
class TranscriptReductionConfig:
    enabled: bool
    top_primary_reasons: int
    samples_per_primary: int
    max_transcript_chars: int


@dataclass(frozen=True)
class TranscriptSummaryConfig:
    call_selection: CallSelectionFilters
    classification: TranscriptClassificationConfig
    report: TranscriptReportConfig
    reduction_recommendations: TranscriptReductionConfig


DEFAULT_CLASSIFICATION = TranscriptClassificationConfig(
    max_transcript_chars=12000,
    concurrency=4,
    store_results=True,
    skip_existing=True,
    sample_limit=None,
)

DEFAULT_REPORT = TranscriptReportConfig(
    top_primary_reasons=15,
    top_secondary_per_primary=8,
    top_tertiary_per_secondary=6,
)

DEFAULT_REDUCTION = TranscriptReductionConfig(
    enabled=True,
    top_primary_reasons=8,
    samples_per_primary=3,
    max_transcript_chars=1200,
)


DEFAULT_TRANSCRIPT_SUMMARY_CONFIG = TranscriptSummaryConfig(
    call_selection=CallSelectionFilters(
        call_direction=DEFAULT_CALL_SELECTION.call_direction,
        skills_include=DEFAULT_CALL_SELECTION.skills_include,
        skills_exclude=DEFAULT_CALL_SELECTION.skills_exclude,
        teams_include=DEFAULT_CALL_SELECTION.teams_include,
        teams_exclude=DEFAULT_CALL_SELECTION.teams_exclude,
        media_types_include=frozenset({"PhoneCall"}),
        media_types_exclude=DEFAULT_CALL_SELECTION.media_types_exclude,
        link_methods=None,
        include_unmatched=True,
    ),
    classification=DEFAULT_CLASSIFICATION,
    report=DEFAULT_REPORT,
    reduction_recommendations=DEFAULT_REDUCTION,
)


def load_transcript_summary_config(
    path: Path,
    *,
    selection_overrides: CallSelectionOverrides | None = None,
) -> TranscriptSummaryConfig:
    path = resolve_summary_config_path(path)
    if not path.is_file():
        config = DEFAULT_TRANSCRIPT_SUMMARY_CONFIG
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = TranscriptSummaryConfig(
            call_selection=_load_transcript_call_selection(raw),
            classification=_parse_classification(raw.get("classification")),
            report=_parse_report(raw.get("report")),
            reduction_recommendations=_parse_reduction(raw.get("reduction_recommendations")),
        )

    if selection_overrides:
        return replace(
            config,
            call_selection=apply_call_selection_overrides(
                config.call_selection,
                selection_overrides,
            ),
        )
    return config


def _load_transcript_call_selection(raw: dict) -> CallSelectionFilters:
    base = _load_call_selection(raw)
    return CallSelectionFilters(
        call_direction=base.call_direction,
        skills_include=base.skills_include,
        skills_exclude=base.skills_exclude,
        teams_include=base.teams_include,
        teams_exclude=base.teams_exclude,
        media_types_include=base.media_types_include or frozenset({"PhoneCall"}),
        media_types_exclude=base.media_types_exclude,
        link_methods=None,
        include_unmatched=True,
    )


def _parse_classification(raw: object) -> TranscriptClassificationConfig:
    if not isinstance(raw, dict):
        return DEFAULT_CLASSIFICATION
    sample_limit = raw.get("sample_limit")
    return TranscriptClassificationConfig(
        max_transcript_chars=int(
            raw.get("max_transcript_chars", DEFAULT_CLASSIFICATION.max_transcript_chars)
        ),
        concurrency=int(raw.get("concurrency", DEFAULT_CLASSIFICATION.concurrency)),
        store_results=bool(raw.get("store_results", DEFAULT_CLASSIFICATION.store_results)),
        skip_existing=bool(raw.get("skip_existing", DEFAULT_CLASSIFICATION.skip_existing)),
        sample_limit=int(sample_limit) if sample_limit is not None else None,
    )


def _parse_report(raw: object) -> TranscriptReportConfig:
    if not isinstance(raw, dict):
        return DEFAULT_REPORT
    return TranscriptReportConfig(
        top_primary_reasons=int(
            raw.get("top_primary_reasons", DEFAULT_REPORT.top_primary_reasons)
        ),
        top_secondary_per_primary=int(
            raw.get("top_secondary_per_primary", DEFAULT_REPORT.top_secondary_per_primary)
        ),
        top_tertiary_per_secondary=int(
            raw.get("top_tertiary_per_secondary", DEFAULT_REPORT.top_tertiary_per_secondary)
        ),
    )


def _parse_reduction(raw: object) -> TranscriptReductionConfig:
    if not isinstance(raw, dict):
        return DEFAULT_REDUCTION
    return TranscriptReductionConfig(
        enabled=bool(raw.get("enabled", DEFAULT_REDUCTION.enabled)),
        top_primary_reasons=int(
            raw.get("top_primary_reasons", DEFAULT_REDUCTION.top_primary_reasons)
        ),
        samples_per_primary=int(
            raw.get("samples_per_primary", DEFAULT_REDUCTION.samples_per_primary)
        ),
        max_transcript_chars=int(
            raw.get("max_transcript_chars", DEFAULT_REDUCTION.max_transcript_chars)
        ),
    )
