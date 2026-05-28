from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select

from orchestration.config import Settings, get_settings
from orchestration.db.schema import CombinedInteractionRow, CxoneTranscriptRow, ZendeskTicketRow
from orchestration.db.session import get_engine, get_session_factory
from orchestration.linking.combined_columns import ensure_combined_interaction_columns
from orchestration.linking.config import load_link_config, resolve_link_config_path
from orchestration.linking.matcher import TicketLinkIndex
from orchestration.linking.merge import build_combined_record
from orchestration.sinks.combined_postgres import PostgresCombinedSink
from orchestration.zendesk.promoted_columns import ensure_promoted_columns


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
) -> CombinedDatasetResult:
    settings = settings or get_settings()
    config_path = _resolve_link_config_path(settings, link_config_path)
    link_config = load_link_config(config_path)

    engine = get_engine(settings.database_url)
    ensure_promoted_columns(engine)
    ensure_combined_interaction_columns(engine)
    session_factory = get_session_factory(settings.database_url)

    with session_factory() as session:
        if rebuild and not dry_run:
            session.execute(delete(CombinedInteractionRow))
            session.commit()

        tickets = list(session.scalars(select(ZendeskTicketRow)).all())
        cxone_query = select(CxoneTranscriptRow)
        if interaction_start is not None:
            cxone_query = cxone_query.where(
                CxoneTranscriptRow.interaction_start >= interaction_start
            )
        if interaction_end is not None:
            cxone_query = cxone_query.where(
                CxoneTranscriptRow.interaction_start <= interaction_end
            )
        cxone_rows = list(session.scalars(cxone_query).all())

    link_index = TicketLinkIndex(tickets, link_config)
    records = []
    by_link_method: dict[str, int] = {}
    matched = 0
    unmatched = 0

    for cxone in cxone_rows:
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
        )
        records.append(record)
        by_link_method[record.link_method] = by_link_method.get(record.link_method, 0) + 1

    if dry_run:
        return CombinedDatasetResult(
            cxone_segments_considered=len(cxone_rows),
            rows_built=len(records),
            rows_upserted=0,
            matched=matched,
            unmatched=unmatched,
            by_link_method=by_link_method,
        )

    sink = PostgresCombinedSink(settings)
    upsert_stats = sink.upsert_records(records)
    return CombinedDatasetResult(
        cxone_segments_considered=len(cxone_rows),
        rows_built=len(records),
        rows_upserted=upsert_stats["upserted"],
        matched=matched,
        unmatched=unmatched,
        by_link_method=by_link_method,
    )


def _resolve_link_config_path(settings: Settings, override: Path | None) -> Path:
    if override is not None:
        return override
    configured = Path(settings.cxone_zendesk_link_path)
    if configured.is_absolute():
        return resolve_link_config_path(configured)
    project_root = Path(__file__).resolve().parents[3]
    return resolve_link_config_path(project_root / configured)
