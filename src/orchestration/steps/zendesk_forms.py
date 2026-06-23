from __future__ import annotations

from dataclasses import dataclass, field

from orchestration.config import Settings, get_settings
from orchestration.sinks.zendesk_forms_postgres import PostgresZendeskFormSink
from orchestration.zendesk.client import ZendeskClient
from orchestration.zendesk.ticket_forms import TicketFormCatalog


@dataclass
class ZendeskFormExtractionResult:
    forms_extracted: int
    forms_upserted: int = 0
    forms: list[tuple[int, str]] = field(default_factory=list)


def run_zendesk_form_extraction(
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
) -> ZendeskFormExtractionResult:
    """Fetch Zendesk ticket form definitions and upsert them into zendesk_ticket_forms."""
    settings = settings or get_settings()
    client = ZendeskClient(settings)
    catalog = TicketFormCatalog.fetch(client)
    records = catalog.to_records()
    forms = [(int(rec["form_id"]), str(rec["name"])) for rec in records]

    if dry_run:
        return ZendeskFormExtractionResult(forms_extracted=len(records), forms=forms)

    sink = PostgresZendeskFormSink(settings)
    stats = sink.upsert_forms(records)
    return ZendeskFormExtractionResult(
        forms_extracted=len(records),
        forms_upserted=stats["upserted"],
        forms=forms,
    )
