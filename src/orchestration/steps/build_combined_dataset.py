from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import delete, select
from sqlalchemy.orm import defer

from orchestration.config import Settings, get_settings
from orchestration.db.schema import CombinedInteractionRow, CxoneTranscriptRow, ZendeskTicketRow
from orchestration.db.session import get_engine, get_session_factory
from orchestration.linking.combined_columns import ensure_combined_interaction_columns
from orchestration.linking.config import load_link_config, resolve_link_config_path
from orchestration.linking.field_normalization import load_field_normalization_config
from orchestration.linking.matcher import TicketLinkIndex
from orchestration.linking.merge import build_combined_record
from orchestration.models import CombinedInteractionRecord
from orchestration.sinks.combined_postgres import PostgresCombinedSink
from orchestration.zendesk.promoted_columns import ensure_promoted_columns

# Full-table cxone loads OOM when transcript_text + raw_metadata are present.
CXONE_BATCH_SIZE = 50


@dataclass
class CombinedDatasetResult:
    cxone_segments_considered: int
    rows_built: int
    rows_upserted: int
    matched: int
    unmatched: int
    by_link_method: dict[str, int]


def run_build_combined_dataset(
    *,
    settings: Settings | None = None,
    interaction_start: datetime | None = None,
    interaction_end: datetime | None = None,
    matched_only: bool = False,
    rebuild: bool = False,
    dry_run: bool = False,
    link_config_path: Path | None = None,
    cxone_batch_size: int = CXONE_BATCH_SIZE,
) -> CombinedDatasetResult:
    settings = settings or get_settings()
    config_path = _resolve_link_config_path(settings, link_config_path)
    link_config = load_link_config(config_path)
    project_root = Path(__file__).resolve().parents[3]
    normalization_config = load_field_normalization_config(
        _resolve_field_normalization_config_path(settings, project_root)
    )

    engine = get_engine(settings.database_url)
    ensure_promoted_columns(engine)
    ensure_combined_interaction_columns(engine)
    session_factory = get_session_factory(settings.database_url)

    with session_factory() as session:
        if rebuild and not dry_run:
            session.execute(delete(CombinedInteractionRow))
            session.commit()

        tickets = list(session.scalars(select(ZendeskTicketRow)).all())

    link_index = TicketLinkIndex(tickets, link_config)
    by_link_method: dict[str, int] = {}
    matched = 0
    unmatched = 0
    cxone_segments_considered = 0
    rows_built = 0
    rows_upserted = 0

    sink = None if dry_run else PostgresCombinedSink(settings)

    with session_factory() as session:
        for cxone_batch in _iter_cxone_batches(
            session,
            interaction_start=interaction_start,
            interaction_end=interaction_end,
            batch_size=cxone_batch_size,
        ):
            records: list[CombinedInteractionRecord] = []
            for cxone in cxone_batch:
                cxone_segments_considered += 1
                resolved = link_index.resolve(cxone)
                if resolved is None or resolved.ticket_id is None:
                    unmatched += 1
                    if matched_only:
                        continue
                    detail_ticket = None
                    phone_call_ticket = (
                        link_index.get_ticket(resolved.phone_call_ticket_id)
                        if resolved and resolved.phone_call_ticket_id is not None
                        else None
                    )
                else:
                    matched += 1
                    detail_ticket = link_index.get_ticket(resolved.ticket_id)
                    phone_call_ticket = (
                        link_index.get_ticket(resolved.phone_call_ticket_id)
                        if resolved.phone_call_ticket_id is not None
                        else None
                    )

                record = build_combined_record(
                    cxone,
                    detail_ticket=detail_ticket,
                    phone_call_ticket=phone_call_ticket,
                    resolved=resolved,
                    field_normalization=normalization_config,
                    project_root=project_root,
                )
                records.append(record)
                by_link_method[record.link_method] = (
                    by_link_method.get(record.link_method, 0) + 1
                )

            rows_built += len(records)
            if not dry_run and records and sink is not None:
                upsert_stats = sink.upsert_records(records)
                rows_upserted += upsert_stats["upserted"]

            session.expunge_all()

    return CombinedDatasetResult(
        cxone_segments_considered=cxone_segments_considered,
        rows_built=rows_built,
        rows_upserted=rows_upserted,
        matched=matched,
        unmatched=unmatched,
        by_link_method=by_link_method,
    )


def _iter_cxone_batches(
    session,
    *,
    interaction_start: datetime | None,
    interaction_end: datetime | None,
    batch_size: int,
) -> Iterator[list[CxoneTranscriptRow]]:
    last_segment_id: str | None = None

    while True:
        stmt = (
            select(CxoneTranscriptRow)
            .options(defer(CxoneTranscriptRow.raw_metadata))
            .order_by(CxoneTranscriptRow.segment_id)
            .limit(batch_size)
        )
        if interaction_start is not None:
            stmt = stmt.where(CxoneTranscriptRow.interaction_start >= interaction_start)
        if interaction_end is not None:
            stmt = stmt.where(CxoneTranscriptRow.interaction_start <= interaction_end)
        if last_segment_id is not None:
            stmt = stmt.where(CxoneTranscriptRow.segment_id > last_segment_id)

        batch = list(session.scalars(stmt).all())
        if not batch:
            break

        yield batch
        last_segment_id = batch[-1].segment_id


def _resolve_link_config_path(settings: Settings, override: Path | None) -> Path:
    if override is not None:
        return override
    configured = Path(settings.cxone_zendesk_link_path)
    if configured.is_absolute():
        return resolve_link_config_path(configured)
    project_root = Path(__file__).resolve().parents[3]
    return resolve_link_config_path(project_root / configured)


def _resolve_field_normalization_config_path(settings: Settings, project_root: Path) -> Path:
    configured = Path(settings.field_normalization_config_path)
    if configured.is_absolute():
        return configured
    return project_root / configured
