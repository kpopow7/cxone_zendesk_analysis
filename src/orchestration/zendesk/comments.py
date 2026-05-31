from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from orchestration.config import Settings
from orchestration.models import TicketCommentRecord
from orchestration.zendesk.client import ZendeskClient


class ZendeskTicketCommentExtractor:
    """Extract Zendesk ticket comments.

    Two modes:
    - Per-ticket comments endpoint (simple, but slow at scale)
    - Incremental ticket events with `include=comment_events` (fast bulk export)
    """

    def __init__(self, settings: Settings, client: ZendeskClient | None = None) -> None:
        self._settings = settings
        self._client = client or ZendeskClient(settings)

    def extract_for_ticket(self, ticket_id: int) -> list[TicketCommentRecord]:
        payload = self._client.get_json(f"/api/v2/tickets/{ticket_id}/comments.json")
        comments = payload.get("comments", [])
        if not isinstance(comments, list):
            return []
        records: list[TicketCommentRecord] = []
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            record = _to_record(ticket_id, comment)
            if record:
                records.append(record)
        return records

    def extract_range_incremental(
        self,
        start: datetime,
        end: datetime,
        *,
        limit_comments: int | None = None,
    ) -> list[TicketCommentRecord]:
        """Bulk extract comment events using Incremental Ticket Event Export.

        Uses `GET /api/v2/incremental/ticket_events?start_time=...&include=comment_events`.
        Note: incremental export paths do not use the `.json` suffix (unlike regular REST resources).
        Filters returned comment events to comment.created_at within [start, end] (UTC).
        """
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if end_utc <= start_utc:
            raise ValueError("--end must be after --start")

        # Zendesk requires start_time to be at least one minute in the past.
        now_utc = datetime.now(timezone.utc)
        latest_allowed = int(now_utc.timestamp()) - 120
        start_time = min(int(start_utc.timestamp()), latest_allowed)

        params: dict[str, Any] = {
            "start_time": start_time,
            "include": "comment_events",
        }

        records: list[TicketCommentRecord] = []
        url: str | None = "/api/v2/incremental/ticket_events"
        max_pages = 100000
        pages = 0

        while url and pages < max_pages:
            pages += 1
            payload = self._client.get_json(url, params=params)
            params = None

            ticket_events = payload.get("ticket_events", [])
            if isinstance(ticket_events, list):
                for event in ticket_events:
                    if not isinstance(event, dict):
                        continue
                    ticket_id = event.get("ticket_id") or event.get("ticketId") or event.get("id")
                    if ticket_id is None:
                        continue
                    child_events = event.get("child_events")
                    if not isinstance(child_events, list):
                        continue
                    for child in child_events:
                        if not isinstance(child, dict):
                            continue
                        event_type = child.get("type") or child.get("event_type")
                        if str(event_type).lower() != "comment":
                            continue
                        record = _to_record(int(ticket_id), child)
                        if not record or record.created_at is None:
                            continue
                        created = _ensure_utc(record.created_at)
                        if created < start_utc or created > end_utc:
                            continue
                        records.append(record)
                        if limit_comments is not None and len(records) >= limit_comments:
                            return records

            # Pagination fields documented for incremental exports
            next_page = payload.get("next_page")
            url = str(next_page) if next_page else None

            # Stop early if payload end_time is past our requested end window.
            end_time = payload.get("end_time")
            if isinstance(end_time, int) and end_time > int(end_utc.timestamp()):
                break

            if payload.get("end_of_stream") is True:
                break

        return records


def _to_record(ticket_id: int, comment: dict[str, Any]) -> TicketCommentRecord | None:
    comment_id = comment.get("id")
    if comment_id is None:
        return None
    via = comment.get("via") if isinstance(comment.get("via"), dict) else {}
    return TicketCommentRecord(
        comment_id=int(comment_id),
        ticket_id=int(ticket_id),
        author_id=_optional_int(comment.get("author_id")),
        created_at=_parse_dt(comment.get("created_at")),
        is_public=comment.get("public") if isinstance(comment.get("public"), bool) else None,
        via_channel=_optional_str(via.get("channel")),
        body=_optional_str(comment.get("body")),
        html_body=_optional_str(comment.get("html_body")),
        plain_body=_optional_str(comment.get("plain_body")),
        raw_metadata={"comment": comment},
    )


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def records_to_json(records: list[TicketCommentRecord], *, indent: int = 2) -> str:
    from dataclasses import asdict

    return json.dumps([asdict(record) for record in records], indent=indent, default=str)

