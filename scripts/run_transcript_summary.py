#!/usr/bin/env python3
"""Transcript-only summary: LLM reasons from cxone_transcripts (primary / secondary / tertiary)."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.call_selection import CallSelectionOverrides  # noqa: E402
from orchestration.analysis.transcript_summary_progress import TranscriptSummaryProgress  # noqa: E402
from orchestration.analysis.timeframes import resolve_time_window  # noqa: E402
from orchestration.analysis.transcript_summary_report import (  # noqa: E402
    format_report_text,
    write_report_json,
    write_report_markdown,
)
from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.steps.transcript_summary import run_transcript_summary_step  # noqa: E402


@click.command()
@click.option(
    "--timeframe",
    "timeframe_preset",
    type=click.Choice(
        ["all", "yesterday", "last-week", "last-7-days"],
        case_sensitive=False,
    ),
    default="last-week",
    help="Preset window (default: last-week = previous Mon–Sun UTC).",
)
@click.option(
    "--start",
    default=None,
    help="Custom range start (ISO-8601, e.g. 2026-03-05T00:00:00Z). Requires --end.",
)
@click.option(
    "--end",
    default=None,
    help="Custom range end (ISO-8601, e.g. 2026-03-11T23:59:59Z). Requires --start.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Path to transcript_summary.json (default from .env).",
)
@click.option("--json-output", type=click.Path(path_type=Path), default=None, help="Write full report JSON.")
@click.option("--markdown-output", type=click.Path(path_type=Path), default=None, help="Write Markdown report.")
@click.option(
    "--reduction-llm/--no-reduction-llm",
    default=None,
    help="LLM recommendations to reduce volume for top primary reasons.",
)
@click.option(
    "--reanalyze",
    is_flag=True,
    default=False,
    help="Re-classify transcripts even if cached in cxone_transcript_analysis.",
)
@click.option(
    "--limit",
    "sample_limit",
    type=int,
    default=None,
    help="Max transcripts to classify this run (for testing).",
)
@click.option(
    "--batch-size",
    type=int,
    default=None,
    help="Rows per DB fetch (default 50 for large/batched runs).",
)
@click.option(
    "--chunk-days",
    type=int,
    default=None,
    help="Split the time window into N-day UTC chunks (recommended for --timeframe all).",
)
@click.option(
    "--commit-every",
    type=int,
    default=None,
    help="Commit to Postgres after this many successful classifications (default 10 in batch mode).",
)
@click.option(
    "--full-report",
    is_flag=True,
    default=False,
    help="Build full reason breakdown at end (slow for large windows; default is classify-only).",
)
@click.option(
    "--call-direction",
    type=click.Choice(["all", "inbound", "outbound"], case_sensitive=False),
    default=None,
    help="Include only calls with this direction (overrides config).",
)
@click.option("--skill", "skills_include", multiple=True, help="Only include these skills (repeatable).")
@click.option("--exclude-skill", "skills_exclude", multiple=True, help="Exclude these skills (repeatable).")
@click.option("--team", "teams_include", multiple=True, help="Only include these teams (repeatable).")
@click.option(
    "--media-type",
    "media_types_include",
    multiple=True,
    help="Only include these media types, e.g. PhoneCall (repeatable).",
)
def main(
    timeframe_preset: str,
    start: str | None,
    end: str | None,
    config_path: Path | None,
    json_output: Path | None,
    markdown_output: Path | None,
    reduction_llm: bool | None,
    reanalyze: bool,
    sample_limit: int | None,
    batch_size: int | None,
    chunk_days: int | None,
    commit_every: int | None,
    full_report: bool,
    call_direction: str | None,
    skills_include: tuple[str, ...],
    skills_exclude: tuple[str, ...],
    teams_include: tuple[str, ...],
    media_types_include: tuple[str, ...],
) -> None:
    """Classify call transcripts and report primary/secondary/tertiary reasons."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    start_dt = parse_iso_datetime(start) if start else None
    end_dt = parse_iso_datetime(end) if end else None
    if (start and not end) or (end and not start):
        raise click.ClickException("Provide both --start and --end for a custom range.")
    if batch_size is not None and batch_size < 1:
        raise click.ClickException("--batch-size must be at least 1.")
    if chunk_days is not None and chunk_days < 1:
        raise click.ClickException("--chunk-days must be at least 1.")
    if commit_every is not None and commit_every < 1:
        raise click.ClickException("--commit-every must be at least 1.")

    try:
        time_window = resolve_time_window(
            preset=timeframe_preset,
            start=start_dt,
            end=end_dt,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    large_window = time_window.is_unbounded or timeframe_preset.lower() == "all"
    if large_window and batch_size is None and chunk_days is None:
        chunk_days = 7
        batch_size = 50
        click.echo(
            "Large window detected — using --chunk-days 7 --batch-size 50 "
            "(override with explicit flags).",
            err=True,
        )
    if (batch_size is not None or chunk_days is not None) and commit_every is None:
        commit_every = 10

    progress = TranscriptSummaryProgress.stderr()
    progress.info(f"Starting transcript summary for: {time_window.label}")

    selection_overrides = _build_selection_overrides(
        call_direction=call_direction,
        skills_include=skills_include,
        skills_exclude=skills_exclude,
        teams_include=teams_include,
        media_types_include=media_types_include,
    )

    result = run_transcript_summary_step(
        settings,
        time_window=time_window,
        config_path=config_path,
        use_reduction_llm=reduction_llm,
        selection_overrides=selection_overrides,
        reanalyze=reanalyze,
        sample_limit=sample_limit,
        batch_size=batch_size,
        chunk_days=chunk_days,
        commit_every=commit_every,
        classify_only=not full_report,
        progress=progress,
    )

    report = result.report
    click.echo(format_report_text(report), nl=False)

    if json_output:
        write_report_json(report, json_output)
        click.echo(f"\nWrote JSON: {json_output}", err=True)
    if markdown_output:
        write_report_markdown(report, markdown_output)
        click.echo(f"Wrote Markdown: {markdown_output}", err=True)


def _build_selection_overrides(
    *,
    call_direction: str | None,
    skills_include: tuple[str, ...],
    skills_exclude: tuple[str, ...],
    teams_include: tuple[str, ...],
    media_types_include: tuple[str, ...],
) -> CallSelectionOverrides | None:
    overrides = CallSelectionOverrides(
        call_direction=call_direction.lower() if call_direction else None,
        skills_include=frozenset(skills_include) if skills_include else None,
        skills_exclude=frozenset(skills_exclude) if skills_exclude else None,
        teams_include=frozenset(teams_include) if teams_include else None,
        media_types_include=frozenset(media_types_include) if media_types_include else None,
    )
    if all(
        getattr(overrides, field) is None
        for field in (
            "call_direction",
            "skills_include",
            "skills_exclude",
            "teams_include",
            "media_types_include",
        )
    ):
        return None
    return overrides


if __name__ == "__main__":
    main()
