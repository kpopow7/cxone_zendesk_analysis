from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orchestration.analysis.config import ReasonField, _parse_column_list, _parse_reason_fields
from orchestration.analysis.disposition_labels import load_disposition_label_config
from orchestration.analysis.reasons import extract_call_reason, extract_disposition


DEFAULT_NORMALIZED_REASON_FIELDS: tuple[ReasonField, ...] = (
    ReasonField("cf_reason_for_contact_consumer", "Reason for contact (Consumer)"),
    ReasonField("cf_reason_for_contact_installerdealer", "Reason for contact (Installer/Dealer)"),
    ReasonField("cf_reason_for_contact_customer_levolor", "Reason for contact (Customer Levolor)"),
    ReasonField("cf_intent", "Intent"),
    ReasonField("cf_i_need_help_with", "I need help with"),
)

DEFAULT_NORMALIZED_DISPOSITION_FIELDS: tuple[str, ...] = (
    "cf_disposition",
    "cf_disposition_consumer",
    "cf_disposition_dealer",
    "cf_disposition_consumer_levolor",
    "cf_disposition_customer_levolor",
)


@dataclass(frozen=True)
class FieldNormalizationConfig:
    reason_fields: tuple[ReasonField, ...]
    disposition_fields: tuple[str, ...]
    disposition_label_map_path: str
    humanize_reason_codes: bool
    disposition_fallback_humanize: bool
    fallback_to_ticket_subject: bool


@dataclass(frozen=True)
class NormalizedZendeskFields:
    call_reason: str | None
    call_reason_code: str | None
    call_reason_source: str | None
    disposition_code: str | None
    disposition_label: str | None
    disposition_source: str | None


DEFAULT_FIELD_NORMALIZATION_CONFIG = FieldNormalizationConfig(
    reason_fields=DEFAULT_NORMALIZED_REASON_FIELDS,
    disposition_fields=DEFAULT_NORMALIZED_DISPOSITION_FIELDS,
    disposition_label_map_path="config/disposition_label_map.json",
    humanize_reason_codes=True,
    disposition_fallback_humanize=True,
    fallback_to_ticket_subject=False,
)


def resolve_field_normalization_config_path(configured_path: Path) -> Path:
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def load_field_normalization_config(path: Path) -> FieldNormalizationConfig:
    path = resolve_field_normalization_config_path(path)
    if not path.is_file():
        return DEFAULT_FIELD_NORMALIZATION_CONFIG

    raw = json.loads(path.read_text(encoding="utf-8"))
    return FieldNormalizationConfig(
        reason_fields=_parse_reason_fields(raw.get("reason_fields"))
        or DEFAULT_NORMALIZED_REASON_FIELDS,
        disposition_fields=_parse_column_list(raw.get("disposition_fields"))
        or DEFAULT_NORMALIZED_DISPOSITION_FIELDS,
        disposition_label_map_path=str(
            raw.get(
                "disposition_label_map_path",
                DEFAULT_FIELD_NORMALIZATION_CONFIG.disposition_label_map_path,
            )
        ).strip(),
        humanize_reason_codes=bool(
            raw.get(
                "humanize_reason_codes",
                DEFAULT_FIELD_NORMALIZATION_CONFIG.humanize_reason_codes,
            )
        ),
        disposition_fallback_humanize=bool(
            raw.get(
                "disposition_fallback_humanize",
                DEFAULT_FIELD_NORMALIZATION_CONFIG.disposition_fallback_humanize,
            )
        ),
        fallback_to_ticket_subject=bool(
            raw.get(
                "fallback_to_ticket_subject",
                DEFAULT_FIELD_NORMALIZATION_CONFIG.fallback_to_ticket_subject,
            )
        ),
    )


def normalize_zendesk_fields(
    promoted_fields: dict,
    *,
    ticket_subject: str | None,
    config: FieldNormalizationConfig,
    project_root: Path | None = None,
) -> NormalizedZendeskFields:
    """Coalesce form-specific Zendesk fields into unified reason/disposition columns."""
    promoted = promoted_fields if isinstance(promoted_fields, dict) else {}

    call_reason, call_reason_source = extract_call_reason(
        promoted,
        reason_fields=config.reason_fields,
        ticket_subject=ticket_subject,
        fallback_to_ticket_subject=config.fallback_to_ticket_subject,
        humanize_codes=config.humanize_reason_codes,
    )

    call_reason_code: str | None = None
    normalized_call_reason: str | None = None
    if call_reason_source is not None:
        if call_reason_source == "ticket_subject":
            call_reason_code = ticket_subject.strip() if ticket_subject else None
        else:
            raw = promoted.get(call_reason_source)
            call_reason_code = str(raw).strip() if raw is not None and str(raw).strip() else None
        if call_reason and call_reason != "(no call reason captured)":
            normalized_call_reason = call_reason

    label_map_path = _resolve_label_map_path(config.disposition_label_map_path, project_root)
    label_config = load_disposition_label_config(label_map_path)
    disposition_code, disposition_label = extract_disposition(
        promoted,
        disposition_fields=config.disposition_fields,
        label_map=label_config.labels,
        fallback_humanize=config.disposition_fallback_humanize and label_config.fallback_humanize,
    )
    disposition_source = _first_populated_column(promoted, config.disposition_fields)

    return NormalizedZendeskFields(
        call_reason=normalized_call_reason,
        call_reason_code=call_reason_code,
        call_reason_source=call_reason_source,
        disposition_code=disposition_code,
        disposition_label=disposition_label,
        disposition_source=disposition_source,
    )


def _first_populated_column(promoted_fields: dict, columns: tuple[str, ...]) -> str | None:
    for column in columns:
        value = promoted_fields.get(column)
        if value is not None and str(value).strip():
            return column
    return None


def _resolve_label_map_path(configured_path: str, project_root: Path | None) -> Path:
    path = Path(configured_path)
    if path.is_file():
        return path
    if project_root is not None:
        candidate = project_root / configured_path
        if candidate.is_file():
            return candidate
    return path
