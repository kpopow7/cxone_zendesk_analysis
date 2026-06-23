#!/usr/bin/env python3
"""Fetch Zendesk ticket form definitions into the zendesk_ticket_forms lookup table.

The chatbot uses this table to group/filter tickets by form type (e.g.
"Assist (Internal)"). Run it once, then re-run whenever forms change in Zendesk.
After running locally, copy the table to Railway with sync_to_railway.py so the
hosted chatbot can see the form names.
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

load_dotenv(ROOT / ".env")

from orchestration.steps.zendesk_forms import run_zendesk_form_extraction  # noqa: E402


@click.command()
@click.option("--dry-run", is_flag=True, help="Fetch and list forms only; do not write to PostgreSQL.")
def main(dry_run: bool) -> None:
    """Sync Zendesk ticket form names into zendesk_ticket_forms."""
    result = run_zendesk_form_extraction(dry_run=dry_run)

    click.echo(f"Ticket forms fetched: {result.forms_extracted}")
    for form_id, name in sorted(result.forms, key=lambda item: item[1].lower()):
        click.echo(f"  {form_id}\t{name}")
    if not dry_run:
        click.echo(f"Forms upserted: {result.forms_upserted}")
        click.echo(
            "\nNext: sync to Railway so the chatbot can group by form type:\n"
            "  python scripts/sync_to_railway.py --tables zendesk_ticket_forms"
        )


if __name__ == "__main__":
    main()
