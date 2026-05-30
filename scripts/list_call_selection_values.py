#!/usr/bin/env python3
"""List distinct call-selection field values from combined_interactions (for building filters)."""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import click
from dotenv import load_dotenv
from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.analysis.reasons import is_inbound  # noqa: E402
from orchestration.analysis.timeframes import resolve_time_window  # noqa: E402
from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.db.schema import CombinedInteractionRow  # noqa: E402
from orchestration.db.session import get_session_factory  # noqa: E402


@click.command()
@click.option(
    "--timeframe",
    "timeframe_preset",
    type=click.Choice(["all", "yesterday", "last-week", "last-7-days"], case_sensitive=False),
    default="all",
)
@click.option("--start", default=None, help="Custom range start (ISO-8601).")
@click.option("--end", default=None, help="Custom range end (ISO-8601).")
@click.option("--top", default=30, show_default=True, help="Max values per category.")
def main(timeframe_preset: str, start: str | None, end: str | None, top: int) -> None:
    """Print skills, teams, media types, link methods, and call directions in the DB."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    start_dt = parse_iso_datetime(start) if start else None
    end_dt = parse_iso_datetime(end) if end else None
    if (start and not end) or (end and not start):
        raise click.ClickException("Provide both --start and --end for a custom range.")

    time_window = resolve_time_window(
        preset=timeframe_preset if not (start_dt or end_dt) else timeframe_preset,
        start=start_dt,
        end=end_dt,
    )

    skills: Counter[str] = Counter()
    teams: Counter[str] = Counter()
    media_types: Counter[str] = Counter()
    link_methods: Counter[str] = Counter()
    directions: Counter[str] = Counter()

    with get_session_factory(settings.database_url)() as session:
        stmt = select(CombinedInteractionRow)
        if time_window.start is not None:
            stmt = stmt.where(CombinedInteractionRow.interaction_start >= time_window.start)
        if time_window.end is not None:
            stmt = stmt.where(CombinedInteractionRow.interaction_start <= time_window.end)
        rows = session.scalars(stmt).all()

        for row in rows:
            if row.skill_name:
                skills[row.skill_name.strip()] += 1
            if row.team_name:
                teams[row.team_name.strip()] += 1
            if row.media_type:
                media_types[row.media_type.strip()] += 1
            link_methods[row.link_method or "unmatched"] += 1
            if is_inbound(row.call_direction):
                directions["inbound"] += 1
            elif row.call_direction and "out" in row.call_direction.lower():
                directions["outbound"] += 1
            else:
                directions[row.call_direction or "unknown"] += 1

    click.echo(f"Period: {time_window.label}")
    click.echo(f"Rows in window: {len(rows)}")
    click.echo("")

    def _section(title: str, counter: Counter[str]) -> None:
        click.echo(title)
        for name, count in counter.most_common(top):
            click.echo(f"  {count:6}  {name}")
        click.echo("")

    _section("Call direction (derived)", directions)
    _section("Skills", skills)
    _section("Teams", teams)
    _section("Media types", media_types)
    _section("Link methods", link_methods)


if __name__ == "__main__":
    main()
