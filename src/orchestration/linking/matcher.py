from __future__ import annotations

from dataclasses import dataclass

from orchestration.db.schema import CxoneTranscriptRow, ZendeskTicketRow
from orchestration.linking.config import LinkConfig, LinkStrategy, ParentTicketResolution
from orchestration.linking.ticket_refs import normalize_link_value, parse_ticket_reference


@dataclass(frozen=True)
class ResolvedLink:
    """CXone segment linked to Zendesk; ticket_id is the parent (detail) ticket when resolved."""

    link_method: str
    link_key: str
    ticket_id: int | None = None
    phone_call_ticket_id: int | None = None
    parent_link_key: str | None = None


class TicketLinkIndex:
    """Indexes Zendesk tickets and resolves CXone segments via phone-call bridge → parent."""

    def __init__(self, tickets: list[ZendeskTicketRow], config: LinkConfig) -> None:
        self._config = config
        self._tickets_by_id: dict[int, ZendeskTicketRow] = {
            int(ticket.ticket_id): ticket for ticket in tickets
        }
        self._call_object_index = self._build_call_object_index(
            tickets, config.parent_ticket_resolution
        )
        self._fallback_indexes = self._build_fallback_indexes(
            tickets, config.fallback_strategies
        )

    def get_ticket(self, ticket_id: int) -> ZendeskTicketRow | None:
        return self._tickets_by_id.get(ticket_id)

    def resolve(self, cxone: CxoneTranscriptRow) -> ResolvedLink | None:
        parent_cfg = self._config.parent_ticket_resolution
        if parent_cfg.enabled:
            resolved = self._resolve_via_parent_ticket(cxone, parent_cfg)
            if resolved is not None:
                return resolved

        return self._resolve_fallback(cxone, self._config.fallback_strategies)

    def _resolve_via_parent_ticket(
        self,
        cxone: CxoneTranscriptRow,
        cfg: ParentTicketResolution,
    ) -> ResolvedLink | None:
        for field_name in cfg.cxone_fields:
            raw = normalize_link_value(getattr(cxone, field_name, None))
            if raw is None:
                continue

            phone_call_ticket_id = self._call_object_index.get(raw)
            if phone_call_ticket_id is None:
                continue

            phone_ticket = self._tickets_by_id[phone_call_ticket_id]
            parent_ref = getattr(phone_ticket, cfg.zendesk_parent_ticket_column, None)
            parent_id = parse_ticket_reference(parent_ref)
            parent_ticket = self._tickets_by_id.get(parent_id) if parent_id is not None else None

            if parent_ticket is not None:
                return ResolvedLink(
                    link_method="call_object_to_parent",
                    link_key=f"{field_name}={raw}",
                    phone_call_ticket_id=phone_call_ticket_id,
                    ticket_id=int(parent_ticket.ticket_id),
                    parent_link_key=f"{cfg.zendesk_parent_ticket_column}={parent_ref}",
                )

            if parent_id is not None:
                return ResolvedLink(
                    link_method="call_object_parent_not_loaded",
                    link_key=f"{field_name}={raw}",
                    phone_call_ticket_id=phone_call_ticket_id,
                    ticket_id=None,
                    parent_link_key=f"{cfg.zendesk_parent_ticket_column}={parent_id}",
                )

            if cfg.require_parent_ticket_field:
                continue

            return ResolvedLink(
                link_method="call_object_phone_ticket_only",
                link_key=f"{field_name}={raw}",
                phone_call_ticket_id=phone_call_ticket_id,
                ticket_id=phone_call_ticket_id,
            )

        return None

    def _resolve_fallback(
        self,
        cxone: CxoneTranscriptRow,
        strategies: tuple[LinkStrategy, ...],
    ) -> ResolvedLink | None:
        for strategy in strategies:
            if strategy.zendesk_source == "ticket_id":
                for field_name in strategy.cxone_fields:
                    raw = normalize_link_value(getattr(cxone, field_name, None))
                    if raw is None:
                        continue
                    try:
                        ticket_id = int(raw)
                    except ValueError:
                        continue
                    if ticket_id in self._tickets_by_id:
                        return ResolvedLink(
                            link_method=strategy.name,
                            link_key=f"{field_name}={raw}",
                            ticket_id=ticket_id,
                        )
                continue

            field_index = self._fallback_indexes.get(strategy.name)
            if not field_index:
                continue
            for field_name in strategy.cxone_fields:
                raw = normalize_link_value(getattr(cxone, field_name, None))
                if raw is None:
                    continue
                ticket_id = field_index.get(raw)
                if ticket_id is not None:
                    return ResolvedLink(
                        link_method=strategy.name,
                        link_key=f"{field_name}={raw}",
                        ticket_id=ticket_id,
                    )
        return None

    @staticmethod
    def _build_call_object_index(
        tickets: list[ZendeskTicketRow],
        cfg: ParentTicketResolution,
    ) -> dict[str, int]:
        """Map call-object id → phone-call bridge ticket (prefers rows with cf_parent_ticket)."""
        index: dict[str, int] = {}
        bridge_rank: dict[str, int] = {}

        for ticket in tickets:
            if cfg.phone_call_form_ids:
                form_id = ticket.ticket_form_id
                if form_id is None or int(form_id) not in cfg.phone_call_form_ids:
                    continue

            value = normalize_link_value(getattr(ticket, cfg.zendesk_call_object_column, None))
            if value is None:
                continue

            has_parent = parse_ticket_reference(
                getattr(ticket, cfg.zendesk_parent_ticket_column, None)
            ) is not None
            rank = 2 if has_parent else 1
            previous_rank = bridge_rank.get(value, 0)
            if rank >= previous_rank:
                index[value] = int(ticket.ticket_id)
                bridge_rank[value] = rank

        return index

    @staticmethod
    def _build_fallback_indexes(
        tickets: list[ZendeskTicketRow],
        strategies: tuple[LinkStrategy, ...],
    ) -> dict[str, dict[str, int]]:
        indexes: dict[str, dict[str, int]] = {}
        for strategy in strategies:
            if strategy.zendesk_source == "ticket_id" or not strategy.zendesk_column:
                continue
            field_index: dict[str, int] = {}
            for ticket in tickets:
                value = normalize_link_value(getattr(ticket, strategy.zendesk_column, None))
                if value is not None:
                    field_index[value] = int(ticket.ticket_id)
            indexes[strategy.name] = field_index
        return indexes
