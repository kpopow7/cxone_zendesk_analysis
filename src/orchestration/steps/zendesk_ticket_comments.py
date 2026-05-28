from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from orchestration.config import Settings, get_settings
from orchestration.db.schema import ZendeskTicketRow
from orchestration.db.session import get_session_factory
from orchestration.sinks.zendesk_comments_postgres import PostgresZendeskCommentSink
from orchestration.zendesk.comments import ZendeskTicketCommentExtractor, records_to_json


@dataclass
class ZendeskCommentExtractionResult:
    comments_extracted: int
    comments_upserted: int = 0
    json_output_path: str | None = None


def run_zendesk_ticket_comment_extraction(
    start: datetime,
    end: datetime,
    *,
    settings: Settings | None = None,
    dry_run: bool = False,
    skip_database: bool = False,
    mode: str = "incremental",
    limit_tickets: int | None = None,
    limit_comments: int | None = None,
    json_output: Path | None = None,
) -> ZendeskCommentExtractionResult:
    """Extract comments.

    - mode=incremental: uses Incremental Ticket Event Export (fast for large volumes)
    - mode=per-ticket: fetches /tickets/{id}/comments.json for tickets in DB (slower)
    """
    settings = settings or get_settings()
    extractor = ZendeskTicketCommentExtractor(settings)

    if mode == "incremental":
        comment_records = extractor.extract_range_incremental(
            start,
            end,
            limit_comments=limit_comments,
        )
    elif mode == "per-ticket":
        session_factory = get_session_factory(settings.database_url)
        with session_factory() as session:
            stmt = (
                select(ZendeskTicketRow.ticket_id)
                .where(ZendeskTicketRow.created_at >= start)
                .where(ZendeskTicketRow.created_at <= end)
                .order_by(ZendeskTicketRow.ticket_id.asc())
            )
            if limit_tickets is not None:
                stmt = stmt.limit(limit_tickets)
            ticket_ids = [row[0] for row in session.execute(stmt).all()]

        comment_records = []
        for ticket_id in ticket_ids:
            comment_records.extend(extractor.extract_for_ticket(int(ticket_id)))
            if limit_comments is not None and len(comment_records) >= limit_comments:
                comment_records = comment_records[:limit_comments]
                break
    else:
        raise ValueError("mode must be one of: incremental, per-ticket")

    json_path: str | None = None
    if json_output is not None:
        json_output.parent.mkdir(parents=True, exist_ok=True)
        json_output.write_text(records_to_json(comment_records), encoding="utf-8")
        json_path = str(json_output)

    if dry_run or skip_database:
        return ZendeskCommentExtractionResult(
            comments_extracted=len(comment_records),
            json_output_path=json_path,
        )

    sink = PostgresZendeskCommentSink(settings)
    upsert_stats = sink.upsert_records(comment_records)
    return ZendeskCommentExtractionResult(
        comments_extracted=len(comment_records),
        comments_upserted=upsert_stats["upserted"],
        json_output_path=json_path,
    )

