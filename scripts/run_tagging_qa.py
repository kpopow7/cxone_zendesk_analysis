#!/usr/bin/env python3
"""Tagging-accuracy QA (P2): does the agent-tagged Zendesk reason match what the call was about?

Read-only. Compares call_reason (agent tag) against the transcript-derived primary_reason, both
mapped through the controlled taxonomy, to flag miscategorized tickets and validate the reason
data the rest of the analysis rests on.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.tagging_qa import (  # noqa: E402
    format_tagging_qa_text,
    run_tagging_qa,
    write_tagging_qa_json,
)
from orchestration.analysis.timeframes import parse_window_bound, resolve_time_window  # noqa: E402
from orchestration.config import get_settings  # noqa: E402


@click.command()
@click.option(
    "--timeframe",
    "timeframe_preset",
    type=click.Choice(["all", "yesterday", "last-week", "last-7-days"], case_sensitive=False),
    default="all",
    help="Preset window on interaction_start (default: all).",
)
@click.option("--start", default=None, help="Custom range start (ISO-8601). Requires --end.")
@click.option("--end", default=None, help="Custom range end (ISO-8601). Requires --start.")
@click.option(
    "--min-volume",
    type=int,
    default=20,
    help="Minimum comparable interactions for a reason to appear in worst-tagged list (default 20).",
)
@click.option("--top", "top_n", type=int, default=15, help="Rows in worst-tagged / mis-tag-pair lists.")
@click.option("--sample-limit", type=int, default=25, help="Number of sample tickets to list for review.")
@click.option("--json-output", type=click.Path(path_type=Path), default=None, help="Write full report JSON.")
def main(
    timeframe_preset: str,
    start: str | None,
    end: str | None,
    min_volume: int,
    top_n: int,
    sample_limit: int,
    json_output: Path | None,
) -> None:
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    if (start and not end) or (end and not start):
        raise click.ClickException("Provide both --start and --end for a custom range.")
    start_dt = parse_window_bound(start, is_end=False) if start else None
    end_dt = parse_window_bound(end, is_end=True) if end else None

    try:
        window = resolve_time_window(preset=timeframe_preset, start=start_dt, end=end_dt)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    report = run_tagging_qa(
        settings,
        start=window.start,
        end=window.end,
        timeframe_label=window.label,
        min_volume=min_volume,
        top_n=top_n,
        sample_limit=sample_limit,
    )

    click.echo(format_tagging_qa_text(report), nl=False)

    if json_output:
        write_tagging_qa_json(report, json_output)
        click.echo(f"\nWrote JSON: {json_output}", err=True)


if __name__ == "__main__":
    main()
