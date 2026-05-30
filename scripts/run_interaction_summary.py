#!/usr/bin/env python3
"""Step 4 CLI: summarize combined_interactions — top call reasons and recommendations."""

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
from orchestration.analysis.report import (  # noqa: E402
    format_report_text,
    write_report_json,
    write_report_markdown,
)
from orchestration.analysis.timeframes import resolve_time_window  # noqa: E402
from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.steps.interaction_summary import run_interaction_summary_step  # noqa: E402


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
@click.option("--start", default=None, help="Custom range start (ISO-8601); overrides preset start.")
@click.option("--end", default=None, help="Custom range end (ISO-8601); overrides preset end.")
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Path to interaction_summary.json (default from .env).",
)
@click.option("--json-output", type=click.Path(path_type=Path), default=None, help="Write full report JSON.")
@click.option("--markdown-output", type=click.Path(path_type=Path), default=None, help="Write Markdown report.")
@click.option(
    "--llm-recommendations/--no-llm-recommendations",
    default=None,
    help="Generate recommendations from transcript samples via OpenAI API (needs OPENAI_API_KEY).",
)
@click.option(
    "--call-direction",
    type=click.Choice(["all", "inbound", "outbound"], case_sensitive=False),
    default=None,
    help="Include only calls with this direction (overrides config).",
)
@click.option(
    "--skill",
    "skills_include",
    multiple=True,
    help="Only include these skills (repeatable; case-insensitive exact match).",
)
@click.option(
    "--exclude-skill",
    "skills_exclude",
    multiple=True,
    help="Exclude these skills (repeatable).",
)
@click.option(
    "--team",
    "teams_include",
    multiple=True,
    help="Only include these teams (repeatable).",
)
@click.option(
    "--media-type",
    "media_types_include",
    multiple=True,
    help="Only include these media types, e.g. PhoneCall (repeatable).",
)
@click.option(
    "--link-method",
    "link_methods",
    multiple=True,
    help="Only include rows with these link methods (repeatable).",
)
@click.option(
    "--include-unmatched/--no-include-unmatched",
    default=None,
    help="Include CXone segments with no Zendesk ticket match.",
)
def main(
    timeframe_preset: str,
    start: str | None,
    end: str | None,
    config_path: Path | None,
    json_output: Path | None,
    markdown_output: Path | None,
    llm_recommendations: bool | None,
    call_direction: str | None,
    skills_include: tuple[str, ...],
    skills_exclude: tuple[str, ...],
    teams_include: tuple[str, ...],
    media_types_include: tuple[str, ...],
    link_methods: tuple[str, ...],
    include_unmatched: bool | None,
) -> None:
    """Analyze combined_interactions and print top issues with recommendations."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    start_dt = parse_iso_datetime(start) if start else None
    end_dt = parse_iso_datetime(end) if end else None
    if (start and not end) or (end and not start):
        raise click.ClickException("Provide both --start and --end for a custom range.")

    try:
        time_window = resolve_time_window(
            preset=timeframe_preset if not (start_dt or end_dt) else timeframe_preset,
            start=start_dt,
            end=end_dt,
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    selection_overrides = _build_selection_overrides(
        call_direction=call_direction,
        skills_include=skills_include,
        skills_exclude=skills_exclude,
        teams_include=teams_include,
        media_types_include=media_types_include,
        link_methods=link_methods,
        include_unmatched=include_unmatched,
    )

    result = run_interaction_summary_step(
        settings,
        time_window=time_window,
        config_path=config_path,
        use_llm_recommendations=llm_recommendations,
        selection_overrides=selection_overrides,
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
    link_methods: tuple[str, ...],
    include_unmatched: bool | None,
) -> CallSelectionOverrides | None:
    overrides = CallSelectionOverrides(
        call_direction=call_direction.lower() if call_direction else None,
        skills_include=frozenset(skills_include) if skills_include else None,
        skills_exclude=frozenset(skills_exclude) if skills_exclude else None,
        teams_include=frozenset(teams_include) if teams_include else None,
        media_types_include=frozenset(media_types_include) if media_types_include else None,
        link_methods=frozenset(link_methods) if link_methods else None,
        include_unmatched=include_unmatched,
    )
    if all(
        getattr(overrides, field) is None
        for field in (
            "call_direction",
            "skills_include",
            "skills_exclude",
            "teams_include",
            "media_types_include",
            "link_methods",
            "include_unmatched",
        )
    ):
        return None
    return overrides


if __name__ == "__main__":
    main()
