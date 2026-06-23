from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from orchestration.zendesk.client import ZendeskClient


@dataclass(frozen=True)
class TicketFormDefinition:
    form_id: int
    name: str
    display_name: str | None
    active: bool
    position: int | None
    raw: dict[str, Any]

    @property
    def best_name(self) -> str:
        """Prefer the agent-facing name, fall back to display name or id."""
        return self.name or self.display_name or f"form_{self.form_id}"


def parse_ticket_forms(raw_forms: list[dict[str, Any]]) -> list[TicketFormDefinition]:
    """Parse raw /api/v2/ticket_forms payloads into definitions (pure helper)."""
    definitions: list[TicketFormDefinition] = []
    for raw in raw_forms:
        if not isinstance(raw, dict):
            continue
        form_id = raw.get("id")
        if form_id is None:
            continue
        name = raw.get("name") or raw.get("display_name") or raw.get("raw_name")
        position = raw.get("position")
        definitions.append(
            TicketFormDefinition(
                form_id=int(form_id),
                name=str(name) if name else "",
                display_name=str(raw["display_name"]) if raw.get("display_name") else None,
                active=bool(raw.get("active", True)),
                position=int(position) if isinstance(position, (int, float)) else None,
                raw=raw,
            )
        )
    return definitions


class TicketFormCatalog:
    """Zendesk ticket form definitions keyed by form id."""

    def __init__(self, forms: list[TicketFormDefinition]) -> None:
        self._by_id = {form.form_id: form for form in forms}

    @classmethod
    def fetch(cls, client: ZendeskClient) -> "TicketFormCatalog":
        raw_forms = client.get_paginated(
            "/api/v2/ticket_forms.json",
            collection_key="ticket_forms",
        )
        return cls(parse_ticket_forms(raw_forms))

    def get(self, form_id: int) -> TicketFormDefinition | None:
        return self._by_id.get(form_id)

    def all_forms(self) -> list[TicketFormDefinition]:
        return sorted(
            self._by_id.values(),
            key=lambda form: (form.position if form.position is not None else 1_000_000, form.best_name.lower()),
        )

    def name_for(self, form_id: int | None) -> str | None:
        if form_id is None:
            return None
        form = self._by_id.get(int(form_id))
        return form.best_name if form else None

    def to_records(self) -> list[dict[str, Any]]:
        return [
            {
                "form_id": form.form_id,
                "name": form.best_name,
                "display_name": form.display_name,
                "active": form.active,
                "position": form.position,
                "raw_metadata": {"ticket_form": form.raw},
            }
            for form in self.all_forms()
        ]
