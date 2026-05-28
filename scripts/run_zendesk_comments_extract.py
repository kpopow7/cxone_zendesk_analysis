#!/usr/bin/env python3
"""Optional (later): extract Zendesk ticket comments into PostgreSQL."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import parse_iso_datetime  # noqa: E402
from orchestration.steps.zendesk_ticket_comments import (  # noqa: E402
    run_zendesk_ticket_comment_extraction,
)


@click.command()
@click.option("--start", required=True, help="ISO start; selects tickets by created_at")
@click.option("--end", required=True, help="ISO end; selects tickets by created_at")
@click.option(
    "--mode",
    type=click.Choice(["incremental", "per-ticket"], case_sensitive=False),
    default="incremental",
    show_default=True,
    help="Comment extraction mode. incremental is fastest at scale.",
)
@click.option("--dry-run", is_flag=True, help="Extract only; do not write to PostgreSQL.")
@click.option("--skip-database", is_flag=True, help="Skip database write; JSON only.")
@click.option("--limit-tickets", type=int, default=None, help="Max tickets to scan (testing).")
@click.option("--limit-comments", type=int, default=None, help="Max comments to store (testing).")
@click.option(
    "--json-output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write extracted records to a JSON file.",
)
def main(
    start: str,
    end: str,
    mode: str,
    dry_run: bool,
    skip_database: bool,
    limit_tickets: int | None,
    limit_comments: int | None,
    json_output: Path | None,
) -> None:
    load_dotenv(ROOT / ".env")
    start_dt = parse_iso_datetime(start)
    end_dt = parse_iso_datetime(end)
    if end_dt <= start_dt:
        raise click.ClickException("--end must be after --start")

    result = run_zendesk_ticket_comment_extraction(
        start_dt,
        end_dt,
        dry_run=dry_run,
        skip_database=skip_database,
        mode=mode,
        limit_tickets=limit_tickets,
        limit_comments=limit_comments,
        json_output=json_output,
    )

    click.echo(f"Comments extracted: {result.comments_extracted}")
    if not dry_run and not skip_database:
        click.echo(f"Comments upserted: {result.comments_upserted}")
    if result.json_output_path:
        click.echo(f"JSON written: {result.json_output_path}")


if __name__ == "__main__":
    main()

