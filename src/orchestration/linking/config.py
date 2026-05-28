from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LinkStrategy:
    name: str
    cxone_fields: tuple[str, ...]
    zendesk_column: str | None = None
    zendesk_source: str | None = None


@dataclass(frozen=True)
class ParentTicketResolution:
    """Match CXone contact ids to a phone-call Zendesk ticket, then resolve cf_parent_ticket."""

    enabled: bool
    cxone_fields: tuple[str, ...]
    zendesk_call_object_column: str
    zendesk_parent_ticket_column: str
    require_parent_ticket_field: bool
    phone_call_form_ids: frozenset[int]


@dataclass(frozen=True)
class LinkConfig:
    parent_ticket_resolution: ParentTicketResolution
    fallback_strategies: tuple[LinkStrategy, ...]


DEFAULT_PARENT_RESOLUTION = ParentTicketResolution(
    enabled=True,
    cxone_fields=("contact_id", "contact_no", "segment_contact_id"),
    zendesk_call_object_column="cf_call_object_identifier",
    zendesk_parent_ticket_column="cf_parent_ticket",
    require_parent_ticket_field=True,
    phone_call_form_ids=frozenset(),
)

DEFAULT_FALLBACK_STRATEGIES: tuple[LinkStrategy, ...] = (
    LinkStrategy("ticket_id", ("ticket_id",), zendesk_source="ticket_id"),
    LinkStrategy(
        "master_call_identifier",
        ("acd_contact_id", "acd_session_id", "segment_contact_id"),
        zendesk_column="cf_master_call_identifier",
    ),
)


def resolve_link_config_path(configured_path: Path) -> Path:
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def load_link_config(path: Path) -> LinkConfig:
    path = resolve_link_config_path(path)
    if not path.is_file():
        return LinkConfig(
            parent_ticket_resolution=DEFAULT_PARENT_RESOLUTION,
            fallback_strategies=DEFAULT_FALLBACK_STRATEGIES,
        )

    raw = json.loads(path.read_text(encoding="utf-8"))
    parent_raw = raw.get("parent_ticket_resolution")
    if isinstance(parent_raw, dict):
        parent = _parse_parent_resolution(parent_raw)
    else:
        parent = DEFAULT_PARENT_RESOLUTION

    strategies_raw = raw.get("fallback_strategies", raw.get("strategies", []))
    strategies = _parse_strategies(strategies_raw)
    return LinkConfig(
        parent_ticket_resolution=parent,
        fallback_strategies=strategies or DEFAULT_FALLBACK_STRATEGIES,
    )


def load_link_strategies(path: Path) -> tuple[LinkStrategy, ...]:
    """Backward-compatible accessor for fallback strategies only."""
    return load_link_config(path).fallback_strategies


def _parse_parent_resolution(raw: dict) -> ParentTicketResolution:
    cxone_fields = raw.get("cxone_fields") or DEFAULT_PARENT_RESOLUTION.cxone_fields
    if not isinstance(cxone_fields, list):
        cxone_fields = list(DEFAULT_PARENT_RESOLUTION.cxone_fields)
    form_ids: list[int] = []
    for value in raw.get("phone_call_form_ids") or []:
        try:
            form_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ParentTicketResolution(
        enabled=bool(raw.get("enabled", True)),
        cxone_fields=tuple(str(field).strip() for field in cxone_fields if str(field).strip()),
        zendesk_call_object_column=str(
            raw.get("zendesk_call_object_column")
            or DEFAULT_PARENT_RESOLUTION.zendesk_call_object_column
        ).strip(),
        zendesk_parent_ticket_column=str(
            raw.get("zendesk_parent_ticket_column")
            or DEFAULT_PARENT_RESOLUTION.zendesk_parent_ticket_column
        ).strip(),
        require_parent_ticket_field=bool(
            raw.get("require_parent_ticket_field", True)
        ),
        phone_call_form_ids=frozenset(form_ids),
    )


def _parse_strategies(entries: object) -> tuple[LinkStrategy, ...]:
    if not isinstance(entries, list):
        return ()
    strategies: list[LinkStrategy] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        cxone_fields = entry.get("cxone_fields") or []
        if not isinstance(cxone_fields, list):
            continue
        fields = tuple(str(field).strip() for field in cxone_fields if str(field).strip())
        if not fields and entry.get("zendesk_source") != "ticket_id":
            continue
        strategies.append(
            LinkStrategy(
                name=name,
                cxone_fields=fields or ("ticket_id",),
                zendesk_column=(
                    str(entry["zendesk_column"]).strip()
                    if entry.get("zendesk_column")
                    else None
                ),
                zendesk_source=(
                    str(entry["zendesk_source"]).strip()
                    if entry.get("zendesk_source")
                    else None
                ),
            )
        )
    return tuple(strategies)
