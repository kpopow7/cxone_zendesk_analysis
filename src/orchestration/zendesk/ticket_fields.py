from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestration.zendesk.client import ZendeskClient
from orchestration.zendesk.field_map import suggested_column_name, slugify_field_title


@dataclass(frozen=True)
class TicketFieldDefinition:
    field_id: int
    title: str
    type: str
    active: bool
    required: bool
    raw: dict[str, Any]

    @property
    def slug(self) -> str:
        return slugify_field_title(self.title, field_id=self.field_id)

    @property
    def suggested_column(self) -> str:
        return suggested_column_name(self.title, field_id=self.field_id)


class TicketFieldCatalog:
    """Zendesk ticket field definitions keyed by field id."""

    def __init__(self, fields: list[TicketFieldDefinition]) -> None:
        self._by_id = {field.field_id: field for field in fields}

    @classmethod
    def fetch(cls, client: ZendeskClient) -> TicketFieldCatalog:
        raw_fields = client.get_paginated(
            "/api/v2/ticket_fields.json",
            collection_key="ticket_fields",
        )
        definitions: list[TicketFieldDefinition] = []
        for raw in raw_fields:
            field_id = raw.get("id")
            title = raw.get("title") or raw.get("raw_title")
            if field_id is None or not title:
                continue
            definitions.append(
                TicketFieldDefinition(
                    field_id=int(field_id),
                    title=str(title),
                    type=str(raw.get("type") or "unknown"),
                    active=bool(raw.get("active", True)),
                    required=bool(raw.get("required", False)),
                    raw=raw,
                )
            )
        return cls(definitions)

    def get(self, field_id: int) -> TicketFieldDefinition | None:
        return self._by_id.get(field_id)

    def all_fields(self) -> list[TicketFieldDefinition]:
        return list(self._by_id.values())

    def active_custom_fields(self) -> list[TicketFieldDefinition]:
        system_types = {"subject", "description", "status", "tickettype", "priority", "group", "assignee"}
        return [
            field
            for field in self._by_id.values()
            if field.active and field.type not in system_types
        ]

    def to_catalog_records(self) -> list[dict[str, Any]]:
        return [
            {
                "field_id": field.field_id,
                "title": field.title,
                "type": field.type,
                "active": field.active,
                "required": field.required,
                "slug": field.slug,
                "suggested_column": field.suggested_column,
            }
            for field in sorted(self._by_id.values(), key=lambda item: item.title.lower())
        ]
