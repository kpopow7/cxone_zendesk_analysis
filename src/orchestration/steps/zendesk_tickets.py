from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from orchestration.config import Settings, get_settings
from orchestration.sinks.zendesk_postgres import PostgresZendeskSink
from orchestration.zendesk.tickets import ZendeskTicketExtractor, records_to_json


@dataclass
class ZendeskExtractionResult:
    records_extracted: int
    records_upserted: int = 0
    json_output_path: str | None = None


def run_zendesk_ticket_extraction(
    start: datetime,
    end: datetime,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    skip_database: bool = False,
    limit: int | None = None,
    json_output: Path | None = None,
) -> ZendeskExtractionResult:
    settings = settings or get_settings()
    extractor = ZendeskTicketExtractor(settings)

    records = extractor.extract(start, end, limit=limit)

    json_path: str | None = None
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(records_to_json(records), encoding="utf-8")
        json_path = str(json_output)

    if dry_run or skip_database:
        return ZendeskExtractionResult(
            records_extracted=len(records),
            json_output_path=json_path,
        )

    sink = PostgresZendeskSink(settings)
    upsert_stats = sink.upsert_records(records)
    return ZendeskExtractionResult(
        records_extracted=len(records),
        records_upserted=upsert_stats["upserted"],
        json_output_path=json_path,
    )
