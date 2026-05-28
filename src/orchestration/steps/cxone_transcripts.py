from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from orchestration.config import Settings, get_settings
from orchestration.cxone.auth import CxoneAuthClient
from orchestration.cxone.transcripts import CxoneTranscriptExtractor, records_to_json
from orchestration.sinks.postgres import PostgresTranscriptSink


@dataclass
class ExtractionResult:
    records_extracted: int
    records_upserted: int = 0
    json_output_path: str | None = None


def run_cxone_transcript_extraction(
    start: datetime,
    end: datetime,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    skip_database: bool = False,
    enrich_transcripts: bool = False,
    limit: int | None = None,
    json_output: Path | None = None,
) -> ExtractionResult:
    settings = settings or get_settings()
    auth = CxoneAuthClient(settings)
    extractor = CxoneTranscriptExtractor(settings, auth)

    records = extractor.extract(
        start,
        end,
        enrich_transcripts=enrich_transcripts,
        limit=limit,
    )

    json_path: str | None = None
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(records_to_json(records), encoding="utf-8")
        json_path = str(json_output)

    if dry_run or skip_database:
        return ExtractionResult(
            records_extracted=len(records),
            json_output_path=json_path,
        )

    sink = PostgresTranscriptSink(settings)
    upsert_stats = sink.upsert_records(records)
    return ExtractionResult(
        records_extracted=len(records),
        records_upserted=upsert_stats["upserted"],
        json_output_path=json_path,
    )
