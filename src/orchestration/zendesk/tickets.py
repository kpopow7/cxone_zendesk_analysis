from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from orchestration.config import Settings
from orchestration.models import TicketRecord
from orchestration.zendesk.client import ZendeskClient
from orchestration.zendesk.field_map import (
    PromotedField,
    load_promoted_fields,
    resolve_field_map_path,
    slugify_field_title,
)
from orchestration.zendesk.ticket_fields import TicketFieldCatalog


class ZendeskTicketExtractor:
    """Extract Zendesk tickets for a created_at date range via the Search API."""

    def __init__(
        self,
        settings: Settings,
        client: ZendeskClient | None = None,
        *,
        field_catalog: TicketFieldCatalog | None = None,
        promoted_fields: list[PromotedField] | None = None,
    ) -> None:
        self._settings = settings
        self._client = client or ZendeskClient(settings)
        self._field_catalog = field_catalog
        self._promoted_fields = promoted_fields

    def extract(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = None,
    ) -> list[TicketRecord]:
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if end_utc <= start_utc:
            raise ValueError("--end must be after --start")

        catalog = self._field_catalog or TicketFieldCatalog.fetch(self._client)
        promoted = (
            self._promoted_fields
            if self._promoted_fields is not None
            else load_promoted_fields(self._resolve_field_map_path())
        )

        records: list[TicketRecord] = []
        for chunk_start, chunk_end in _chunk_date_range(start_utc, end_utc):
            remaining = None if limit is None else limit - len(records)
            for ticket in self._search_tickets(
                chunk_start, chunk_end, limit=remaining
            ):
                created_at = _parse_dt(ticket.get("created_at"))
                if created_at is None or created_at < start_utc or created_at > end_utc:
                    continue
                records.append(self._to_record(ticket, catalog, promoted))
                if limit is not None and len(records) >= limit:
                    return records

        return records

    def _resolve_field_map_path(self) -> Path:
        configured = Path(self._settings.zendesk_field_map_path)
        if not configured.is_absolute():
            configured = Path(__file__).resolve().parents[3] / configured
        return resolve_field_map_path(configured)

    def _search_tickets(
        self,
        start: datetime,
        end: datetime,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        # Export search supports >1000 results; do not put type:ticket in query (use filter[type]).
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        query = build_created_at_date_filters(start, end)
        page_size = 100
        params: dict[str, Any] = {
            "query": query,
            "filter[type]": "ticket",
            "page[size]": page_size,
        }
        tickets: list[dict[str, Any]] = []
        after_cursor: str | None = None

        while True:
            request_params = dict(params)
            if after_cursor:
                request_params["page[after]"] = after_cursor

            payload = self._client.get_json("/api/v2/search/export.json", params=request_params)
            results = payload.get("results", [])
            if isinstance(results, list):
                for item in results:
                    if not isinstance(item, dict):
                        continue
                    created_at = _parse_dt(item.get("created_at"))
                    if created_at is None or created_at < start_utc or created_at > end_utc:
                        continue
                    tickets.append(item)
                    if limit is not None and len(tickets) >= limit:
                        return tickets

            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            if not meta.get("has_more"):
                break
            after_cursor = meta.get("after_cursor")
            if not after_cursor:
                break

        return tickets

    def _to_record(
        self,
        ticket: dict[str, Any],
        catalog: TicketFieldCatalog,
        promoted: list[PromotedField],
    ) -> TicketRecord:
        ticket_id = int(ticket["id"])
        via = ticket.get("via") if isinstance(ticket.get("via"), dict) else {}
        custom_fields, promoted_values = _parse_custom_fields(
            ticket.get("custom_fields"),
            catalog,
            promoted,
        )

        return TicketRecord(
            ticket_id=ticket_id,
            url=_optional_str(ticket.get("url")),
            external_id=_optional_str(ticket.get("external_id")),
            subject=_optional_str(ticket.get("subject")),
            description=_optional_str(ticket.get("description")),
            status=_optional_str(ticket.get("status")),
            priority=_optional_str(ticket.get("priority")),
            ticket_type=_optional_str(ticket.get("type")),
            tags=_normalize_tags(ticket.get("tags")),
            created_at=_parse_dt(ticket.get("created_at")),
            updated_at=_parse_dt(ticket.get("updated_at")),
            due_at=_parse_dt(ticket.get("due_at")),
            requester_id=_optional_int(ticket.get("requester_id")),
            submitter_id=_optional_int(ticket.get("submitter_id")),
            assignee_id=_optional_int(ticket.get("assignee_id")),
            organization_id=_optional_int(ticket.get("organization_id")),
            group_id=_optional_int(ticket.get("group_id")),
            brand_id=_optional_int(ticket.get("brand_id")),
            ticket_form_id=_optional_int(ticket.get("ticket_form_id")),
            via_channel=_optional_str(via.get("channel")),
            recipient=_optional_str(ticket.get("recipient")),
            is_public=ticket.get("is_public") if isinstance(ticket.get("is_public"), bool) else None,
            has_incidents=ticket.get("has_incidents")
            if isinstance(ticket.get("has_incidents"), bool)
            else None,
            custom_fields=custom_fields,
            promoted_fields=promoted_values,
            raw_metadata={"ticket": ticket},
        )


def _parse_custom_fields(
    raw_fields: Any,
    catalog: TicketFieldCatalog,
    promoted: list[PromotedField],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Build slug-keyed custom_fields and column-keyed promoted_fields dicts."""
    custom_fields: dict[str, Any] = {}
    promoted_values: dict[str, Any] = {}
    promoted_by_id = {field.field_id: field.column for field in promoted}

    if not isinstance(raw_fields, list):
        return custom_fields, promoted_values

    for entry in raw_fields:
        if not isinstance(entry, dict):
            continue
        field_id = entry.get("id")
        if field_id is None:
            continue
        field_id = int(field_id)
        value = _normalize_custom_field_value(entry.get("value"))
        definition = catalog.get(field_id)
        key = definition.slug if definition else slugify_field_title(f"field_{field_id}", field_id=field_id)
        custom_fields[key] = value

        column = promoted_by_id.get(field_id)
        if column:
            promoted_values[column] = value

    return custom_fields, promoted_values


def _normalize_custom_field_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        if isinstance(value, str) and not value.strip():
            return None
        return value
    if isinstance(value, list):
        return [item for item in value if item is not None]
    return str(value)


def _normalize_tags(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(tag) for tag in value if tag is not None and str(tag).strip()]


def _chunk_date_range(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    chunks: list[tuple[datetime, datetime]] = []
    current_date = start_utc.date()
    while current_date <= end_utc.date():
        day_start = datetime.combine(current_date, datetime.min.time(), tzinfo=timezone.utc)
        day_end = datetime.combine(current_date, datetime.max.time(), tzinfo=timezone.utc)
        chunk_start = max(day_start, start_utc)
        chunk_end = min(day_end, end_utc)
        if chunk_start <= chunk_end:
            chunks.append((chunk_start, chunk_end))
        current_date += timedelta(days=1)
    return chunks or [(start_utc, end_utc)]


def build_created_at_date_filters(start: datetime, end: datetime) -> str:
    """Date-only created> / created< filters (Zendesk does not support >= or <=)."""
    start_utc = _ensure_utc(start)
    end_utc = _ensure_utc(end)
    lower_exclusive = start_utc.date() - timedelta(days=1)
    upper_exclusive = end_utc.date() + timedelta(days=1)
    return (
        f"created>{lower_exclusive.isoformat()} "
        f"created<{upper_exclusive.isoformat()}"
    )


def build_created_at_search_query(start: datetime, end: datetime) -> str:
    """Full query for GET /api/v2/search.json (includes type:ticket)."""
    return f"type:ticket {build_created_at_date_filters(start, end)}"


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_dt(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


def records_to_json(records: list[TicketRecord], *, indent: int = 2) -> str:
    from dataclasses import asdict

    return json.dumps([asdict(record) for record in records], indent=indent, default=str)
