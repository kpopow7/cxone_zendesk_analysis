from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestration.analysis.call_selection import CallSelectionOverrides
from orchestration.analysis.timeframes import TimeWindow
from orchestration.analysis.transcript_summary_progress import TranscriptSummaryProgress
from orchestration.analysis.transcript_summary_report import (
    TranscriptSummaryReport,
    run_transcript_summary,
)
from orchestration.config import Settings


@dataclass(frozen=True)
class TranscriptSummaryResult:
    report: TranscriptSummaryReport


def run_transcript_summary_step(
    settings: Settings,
    *,
    time_window: TimeWindow,
    config_path: Path | None = None,
    use_reduction_llm: bool | None = None,
    selection_overrides: CallSelectionOverrides | None = None,
    reanalyze: bool = False,
    sample_limit: int | None = None,
    batch_size: int | None = None,
    chunk_days: int | None = None,
    commit_every: int | None = None,
    classify_only: bool = False,
    progress: TranscriptSummaryProgress | None = None,
) -> TranscriptSummaryResult:
    report = run_transcript_summary(
        settings,
        time_window=time_window,
        config_path=config_path,
        use_reduction_llm=use_reduction_llm,
        selection_overrides=selection_overrides,
        reanalyze=reanalyze,
        sample_limit=sample_limit,
        batch_size=batch_size,
        chunk_days=chunk_days,
        commit_every=commit_every,
        classify_only=classify_only,
        progress=progress,
    )
    return TranscriptSummaryResult(report=report)
