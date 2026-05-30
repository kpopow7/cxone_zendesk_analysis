from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestration.analysis.call_selection import CallSelectionOverrides
from orchestration.analysis.report import InteractionSummaryReport, run_interaction_summary
from orchestration.analysis.timeframes import TimeWindow
from orchestration.config import Settings


@dataclass(frozen=True)
class InteractionSummaryResult:
    report: InteractionSummaryReport


def run_interaction_summary_step(
    settings: Settings,
    *,
    time_window: TimeWindow,
    config_path: Path | None = None,
    use_llm_recommendations: bool | None = None,
    selection_overrides: CallSelectionOverrides | None = None,
) -> InteractionSummaryResult:
    report = run_interaction_summary(
        settings,
        time_window=time_window,
        config_path=config_path,
        use_llm_recommendations=use_llm_recommendations,
        selection_overrides=selection_overrides,
    )
    return InteractionSummaryResult(report=report)
