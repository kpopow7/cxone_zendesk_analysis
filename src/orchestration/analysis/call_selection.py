from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from typing import Protocol

from orchestration.db.schema import CombinedInteractionRow


class SegmentFilterRow(Protocol):
    call_direction: str | None
    skill_name: str | None
    team_name: str | None
    media_type: str | None


def _is_inbound(call_direction: str | None) -> bool:
    if not call_direction:
        return False
    normalized = call_direction.upper().replace("-", "_")
    return "IN_BOUND" in normalized or normalized == "INBOUND"


@dataclass(frozen=True)
class CallSelectionFilters:
    """Criteria for which combined_interactions rows are included in the summary."""

    call_direction: str = "inbound"  # all | inbound | outbound
    skills_include: frozenset[str] = frozenset()
    skills_exclude: frozenset[str] = frozenset()
    teams_include: frozenset[str] = frozenset()
    teams_exclude: frozenset[str] = frozenset()
    media_types_include: frozenset[str] = frozenset()
    media_types_exclude: frozenset[str] = frozenset()
    link_methods: frozenset[str] | None = frozenset({"call_object_to_parent"})
    include_unmatched: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_direction": self.call_direction,
            "skills_include": sorted(self.skills_include),
            "skills_exclude": sorted(self.skills_exclude),
            "teams_include": sorted(self.teams_include),
            "teams_exclude": sorted(self.teams_exclude),
            "media_types_include": sorted(self.media_types_include),
            "media_types_exclude": sorted(self.media_types_exclude),
            "link_methods": sorted(self.link_methods) if self.link_methods is not None else None,
            "include_unmatched": self.include_unmatched,
        }


@dataclass(frozen=True)
class CallSelectionOverrides:
    """CLI or runtime overrides; None fields leave config unchanged."""

    call_direction: str | None = None
    skills_include: frozenset[str] | None = None
    skills_exclude: frozenset[str] | None = None
    teams_include: frozenset[str] | None = None
    teams_exclude: frozenset[str] | None = None
    media_types_include: frozenset[str] | None = None
    media_types_exclude: frozenset[str] | None = None
    link_methods: frozenset[str] | None = None
    include_unmatched: bool | None = None


DEFAULT_CALL_SELECTION = CallSelectionFilters()


def apply_call_selection_overrides(
    base: CallSelectionFilters,
    overrides: CallSelectionOverrides | None,
) -> CallSelectionFilters:
    if overrides is None:
        return base
    updates: dict[str, Any] = {}
    for field_name in (
        "call_direction",
        "skills_include",
        "skills_exclude",
        "teams_include",
        "teams_exclude",
        "media_types_include",
        "media_types_exclude",
        "link_methods",
        "include_unmatched",
    ):
        value = getattr(overrides, field_name)
        if value is not None:
            updates[field_name] = value
    return replace(base, **updates) if updates else base


def _normalize_token(value: str | None) -> str:
    return " ".join(str(value or "").split()).casefold()


def _matches_include_exclude(
    value: str | None,
    *,
    include: frozenset[str],
    exclude: frozenset[str],
) -> bool:
    if exclude:
        normalized = _normalize_token(value)
        if normalized and normalized in {_normalize_token(item) for item in exclude}:
            return False
        if not value and "" in exclude:
            return False

    if not include:
        return True

    if not value or not str(value).strip():
        return False

    normalized = _normalize_token(value)
    allowed = {_normalize_token(item) for item in include}
    return normalized in allowed


def _direction_category(call_direction: str | None) -> str:
    if not call_direction:
        return "unknown"
    normalized = call_direction.upper().replace("-", "_")
    if "IN_BOUND" in normalized or normalized == "INBOUND":
        return "inbound"
    if "OUT_BOUND" in normalized or normalized == "OUTBOUND":
        return "outbound"
    return "other"


def _matches_call_direction_row(row: SegmentFilterRow, mode: str) -> bool:
    normalized_mode = mode.strip().lower()
    if normalized_mode in ("all", "any", ""):
        return True
    if normalized_mode == "inbound":
        return _is_inbound(row.call_direction)
    if normalized_mode == "outbound":
        return _direction_category(row.call_direction) == "outbound"
    return True


def _matches_call_direction(row: CombinedInteractionRow, mode: str) -> bool:
    return _matches_call_direction_row(row, mode)


def _matches_link_method(row: CombinedInteractionRow, filters: CallSelectionFilters) -> bool:
    link_method = row.link_method or "unmatched"
    if filters.link_methods is None:
        if not filters.include_unmatched and row.ticket_id is None:
            return False
        return True

    if link_method in filters.link_methods:
        return True
    if filters.include_unmatched and link_method == "unmatched":
        return True
    return False


def row_matches_segment_filters(
    row: SegmentFilterRow,
    filters: CallSelectionFilters,
) -> bool:
    """Direction, skill, team, and media filters (no Zendesk link_method)."""
    if not _matches_call_direction_row(row, filters.call_direction):
        return False
    if not _matches_include_exclude(
        row.skill_name,
        include=filters.skills_include,
        exclude=filters.skills_exclude,
    ):
        return False
    if not _matches_include_exclude(
        row.team_name,
        include=filters.teams_include,
        exclude=filters.teams_exclude,
    ):
        return False
    if not _matches_include_exclude(
        row.media_type,
        include=filters.media_types_include,
        exclude=filters.media_types_exclude,
    ):
        return False
    return True


def row_matches_call_selection(
    row: CombinedInteractionRow,
    filters: CallSelectionFilters,
) -> bool:
    if not row_matches_segment_filters(row, filters):
        return False
    if not _matches_link_method(row, filters):
        return False
    return True


def exclusion_summary(filters: CallSelectionFilters) -> str:
    parts: list[str] = []
    if filters.call_direction != "all":
        parts.append(f"call_direction={filters.call_direction}")
    if filters.skills_include:
        parts.append(f"skills={len(filters.skills_include)} included")
    if filters.skills_exclude:
        parts.append(f"skills_exclude={len(filters.skills_exclude)}")
    if filters.teams_include:
        parts.append(f"teams={len(filters.teams_include)} included")
    if filters.media_types_include:
        parts.append(f"media_types={len(filters.media_types_include)} included")
    if filters.link_methods is not None:
        parts.append(f"link_methods={len(filters.link_methods)}")
    return ", ".join(parts) if parts else "none"
