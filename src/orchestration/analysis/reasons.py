from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

from orchestration.analysis.config import ReasonField
from orchestration.analysis.disposition_labels import resolve_disposition_label


_UNKNOWN_REASON = "(no call reason captured)"


def normalize_reason_key(value: str) -> str:
    collapsed = " ".join(value.split()).strip().lower()
    return collapsed or _UNKNOWN_REASON


def display_reason(value: str) -> str:
    collapsed = " ".join(value.split()).strip()
    return collapsed or _UNKNOWN_REASON


@dataclass
class InteractionSlice:
    segment_id: str
    interaction_start: object
    link_method: str
    call_reason: str
    call_reason_key: str
    call_reason_source: str | None
    disposition_code: str | None
    disposition_label: str | None
    transcript_text: str
    ticket_subject: str | None
    segment_summary: str | None
    client_sentiment: str | None
    ticket_priority: str | None
    skill_name: str | None
    team_name: str | None


@dataclass
class ReasonBucket:
    reason_key: str
    reason: str
    count: int
    share_pct: float
    importance_score: float
    source_field_counts: Counter[str] = field(default_factory=Counter)
    negative_sentiment_pct: float = 0.0
    high_priority_pct: float = 0.0
    top_skills: list[tuple[str, int]] = field(default_factory=list)
    sample_subjects: list[str] = field(default_factory=list)
    sample_summaries: list[str] = field(default_factory=list)
    sample_segment_ids: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    recommendation_source: str = "rules"


_SUBJECT_NOISE = re.compile(
    r"^(order\s*#?\s*\d+|\d{6,}|[a-f0-9-]{20,})$",
    re.IGNORECASE,
)

_CODE_PREFIXES = (
    "reasoncontactdi_",
    "levreasoncontactdi_",
    "dispdealer_",
    "dispdealer__",
    "dispconlev_",
    "dispcon_",
    "levdispdealer_",
    "intent__",
)


def humanize_reason_code(value: str) -> str:
    text = value.strip()
    lowered = text.lower()
    for prefix in _CODE_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break
    text = text.replace("__", " / ").replace("_", " ")
    return " ".join(text.split())


def looks_like_reason_code(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(_CODE_PREFIXES) or "__" in lowered


def extract_call_reason(
    promoted_fields: dict,
    *,
    reason_fields: tuple[ReasonField, ...],
    ticket_subject: str | None,
    fallback_to_ticket_subject: bool = False,
    humanize_codes: bool = True,
) -> tuple[str, str | None]:
    for reason_field in reason_fields:
        value = promoted_fields.get(reason_field.column)
        if value and str(value).strip():
            raw = display_reason(str(value))
            if humanize_codes and looks_like_reason_code(raw):
                return humanize_reason_code(raw), reason_field.column
            return raw, reason_field.column

    if fallback_to_ticket_subject and ticket_subject and str(ticket_subject).strip():
        subject = display_reason(str(ticket_subject))
        if not _SUBJECT_NOISE.match(subject):
            return subject, "ticket_subject"

    return _UNKNOWN_REASON, None


def extract_disposition(
    promoted_fields: dict,
    *,
    disposition_fields: tuple[str, ...],
    label_map: dict[str, str] | None = None,
    fallback_humanize: bool = True,
) -> tuple[str | None, str | None]:
    """Return (raw_code, display_label) from the first populated disposition field."""
    for column in disposition_fields:
        value = promoted_fields.get(column)
        if value and str(value).strip():
            code = display_reason(str(value))
            label = resolve_disposition_label(
                code,
                label_map or {},
                fallback_humanize=fallback_humanize,
            )
            return code, label
    return None, None


_NEGATIVE_SENTIMENT = re.compile(
    r"\b(negative|very\s*negative|dissatisfied|angry|upset|frustrated)\b",
    re.IGNORECASE,
)

_HIGH_PRIORITY = frozenset({"urgent", "high"})


def is_negative_sentiment(value: str | None) -> bool:
    if not value:
        return False
    return bool(_NEGATIVE_SENTIMENT.search(value))


def is_high_priority(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().lower() in _HIGH_PRIORITY


def is_inbound(call_direction: str | None) -> bool:
    if not call_direction:
        return False
    normalized = call_direction.upper().replace("-", "_")
    return "IN_BOUND" in normalized or normalized == "INBOUND"
