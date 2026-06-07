#!/usr/bin/env python3
"""Step 3 CLI: link CXone transcripts to Zendesk tickets and build combined_interactions."""

from __future__ import annotations

import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.steps.build_combined_dataset import run_build_combined_dataset  # noqa: E402


@click.command()
@click.option(
    "--interaction-start",
    default=None,
    help="Only CXone segments with interaction_start on/after this time (ISO-8601).",
)
@click.option(
    "--interaction-end",
    default=None,
    help="Only CXone segments with interaction_start on/before this time (ISO-8601).",
)
@click.option(
    "--matched-only",
    is_flag=True,
    help="Omit rows with no Zendesk ticket match.",
)
@click.option(
    "--rebuild",
    is_flag=True,
    help="Delete all combined_interactions rows before rebuilding.",
)
@click.option(
    "--batch-size",
    "cxone_batch_size",
    type=int,
    default=50,
    show_default=True,
    help="CXone segments per read batch (lower if Postgres runs out of memory).",
)
@click.option("--dry-run", is_flag=True, help="Compute link stats only; do not write to PostgreSQL.")
@click.option(
    "--link-config",
    type=click.Path(path_type=Path, exists=True),
    default=None,
    help="Override path to cxone_zendesk_link.json (default from .env).",
)
def main(
    interaction_start: str | None,
    interaction_end: str | None,
    matched_only: bool,
    rebuild: bool,
    cxone_batch_size: int,
    dry_run: bool,
    link_config: Path | None,
) -> None:
    """Build combined_interactions from cxone_transcripts + zendesk_tickets."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()

    start_dt = parse_iso_datetime(interaction_start) if interaction_start else None
    end_dt = parse_iso_datetime(interaction_end) if interaction_end else None
    if start_dt and end_dt and end_dt <= start_dt:
        raise click.ClickException("--interaction-end must be after --interaction-start")

    result = run_build_combined_dataset(
        settings=settings,
        interaction_start=start_dt,
        interaction_end=end_dt,
        matched_only=matched_only,
        rebuild=rebuild,
        dry_run=dry_run,
        link_config_path=link_config,
        cxone_batch_size=cxone_batch_size,
    )

    click.echo(f"CXone segments considered: {result.cxone_segments_considered}")
    click.echo(f"Rows built: {result.rows_built}")
    click.echo(f"Matched to Zendesk: {result.matched}")
    click.echo(f"Unmatched: {result.unmatched}")
    for method, count in sorted(result.by_link_method.items()):
        click.echo(f"  {method}: {count}")
    if not dry_run:
        click.echo(f"Rows upserted: {result.rows_upserted}")


if __name__ == "__main__":
    main()
