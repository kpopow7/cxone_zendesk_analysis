#!/usr/bin/env python3
"""Discover Zendesk ticket fields and sample tickets for column promotion planning."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.config import get_settings, parse_iso_datetime  # noqa: E402
from orchestration.zendesk.client import ZendeskClient  # noqa: E402
from orchestration.zendesk.field_map import load_promoted_fields, resolve_field_map_path  # noqa: E402
from orchestration.zendesk.ticket_fields import TicketFieldCatalog  # noqa: E402
from orchestration.zendesk.tickets import build_created_at_search_query  # noqa: E402


def _default_catalog_path() -> Path:
    return ROOT / "output" / "zendesk_ticket_fields.json"


def _default_map_path() -> Path:
    return resolve_field_map_path(ROOT / "config" / "zendesk_field_map.json")


@click.command()
@click.option("--start", default=None, help="Optional ISO start for sample ticket search")
@click.option("--end", default=None, help="Optional ISO end for sample ticket search")
@click.option(
    "--catalog-output",
    type=click.Path(path_type=Path),
    default=None,
    help="Write full field catalog JSON (default: output/zendesk_ticket_fields.json)",
)
@click.option(
    "--write-example-map",
    is_flag=True,
    help="Write config/zendesk_field_map.json.example with suggested columns",
)
@click.option("--active-only", is_flag=True, default=True, help="List active custom fields only")
def main(
    start: str | None,
    end: str | None,
    catalog_output: Path | None,
    write_example_map: bool,
    active_only: bool,
) -> None:
    """Probe Zendesk auth, ticket_fields, and optional sample tickets."""
    load_dotenv(ROOT / ".env")
    settings = get_settings()
    client = ZendeskClient(settings)

    click.echo("=== Zendesk connection ===")
    me = client.get_json("/api/v2/users/me.json").get("user", {})
    click.echo(f"subdomain: {settings.zendesk_subdomain}")
    click.echo(f"base_url: {client.base_url}")
    click.echo(f"authenticated_as: {me.get('email') or me.get('name') or me.get('id')}")
    click.echo()

    catalog = TicketFieldCatalog.fetch(client)
    fields = catalog.active_custom_fields() if active_only else catalog.all_fields()

    catalog_path = catalog_output or _default_catalog_path()
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "field_count": len(catalog.to_catalog_records()),
        "active_custom_field_count": len(catalog.active_custom_fields()),
        "fields": catalog.to_catalog_records(),
    }
    catalog_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    click.echo(f"Wrote field catalog: {catalog_path}")
    click.echo()

    click.echo("=== Custom fields (promotion candidates) ===")
    click.echo(f"{'ID':>12}  {'TYPE':<16}  {'SUGGESTED COLUMN':<32}  TITLE")
    for field in sorted(fields, key=lambda item: item.title.lower()):
        click.echo(
            f"{field.field_id:>12}  {field.type:<16}  {field.suggested_column:<32}  {field.title}"
        )
    click.echo()

    promoted = load_promoted_fields(_default_map_path())
    if promoted:
        click.echo("=== Promoted fields (config/zendesk_field_map.json) ===")
        for field in promoted:
            definition = catalog.get(field.field_id)
            title = definition.title if definition else "?"
            click.echo(f"  {field.column} <- field_id {field.field_id} ({title})")
        click.echo()

    if write_example_map:
        example_path = ROOT / "config" / "zendesk_field_map.json.example"
        example = {
            "promoted_fields": [
                {
                    "field_id": field.field_id,
                    "column": field.suggested_column,
                    "title": field.title,
                }
                for field in catalog.active_custom_fields()[:10]
            ]
        }
        example_path.parent.mkdir(parents=True, exist_ok=True)
        example_path.write_text(json.dumps(example, indent=2), encoding="utf-8")
        click.echo(f"Wrote example map: {example_path}")
        click.echo("Copy to config/zendesk_field_map.json and edit promoted_fields.")
        click.echo()

    if start and end:
        start_dt = parse_iso_datetime(start)
        end_dt = parse_iso_datetime(end)
        query = build_created_at_search_query(start_dt, end_dt)
        click.echo(f"search query: {query}")
        search = client.get_json(
            "/api/v2/search.json",
            params={"query": query, "page": 1, "per_page": 3},
        )
        results = search.get("results", [])
        click.echo(f"=== Sample tickets ({len(results)} of count={search.get('count')}) ===")
        for ticket in results[:3]:
            if not isinstance(ticket, dict):
                continue
            click.echo(
                f"  id={ticket.get('id')} status={ticket.get('status')} "
                f"created_at={ticket.get('created_at')} subject={str(ticket.get('subject', ''))[:60]}"
            )
            custom = ticket.get("custom_fields")
            if isinstance(custom, list):
                non_empty = [
                    entry
                    for entry in custom
                    if isinstance(entry, dict) and entry.get("value") not in (None, "", [])
                ]
                click.echo(f"    custom_fields with values: {len(non_empty)}")
        click.echo()


if __name__ == "__main__":
    main()
