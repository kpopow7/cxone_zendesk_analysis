from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path

from orchestration.analysis.call_selection import (
    DEFAULT_CALL_SELECTION,
    CallSelectionFilters,
    CallSelectionOverrides,
    apply_call_selection_overrides,
)


@dataclass(frozen=True)
class ReasonField:
    column: str
    label: str


DEFAULT_CALL_REASON_FIELDS: tuple[ReasonField, ...] = (
    ReasonField("cf_reason_for_contact_consumer", "Reason for contact (Consumer)"),
    ReasonField("cf_reason_for_contact_installerdealer", "Reason for contact (Installer/Dealer)"),
    ReasonField("cf_reason_for_contact_customer_levolor", "Reason for contact (Customer Levolor)"),
    ReasonField("cf_disposition", "Disposition"),
    ReasonField("cf_disposition_consumer", "Disposition (Consumer)"),
    ReasonField("cf_disposition_dealer", "Disposition (Dealer)"),
    ReasonField("cf_disposition_consumer_levolor", "Disposition (Consumer) Levolor"),
    ReasonField("cf_disposition_customer_levolor", "Disposition (Customer) Levolor"),
    ReasonField("cf_intent", "Intent"),
    ReasonField("cf_i_need_help_with", "I need help with"),
)

DEFAULT_DISPOSITION_FIELDS: tuple[str, ...] = (
    "cf_disposition",
    "cf_disposition_consumer",
    "cf_disposition_dealer",
    "cf_disposition_consumer_levolor",
    "cf_disposition_customer_levolor",
)


@dataclass(frozen=True)
class LlmRecommendationConfig:
    enabled: bool
    top_reasons: int
    transcripts_per_reason: int
    max_transcript_chars: int
    max_summary_chars: int


@dataclass(frozen=True)
class SummaryAnalysisConfig:
    call_reason_fields: tuple[ReasonField, ...]
    disposition_fields: tuple[str, ...]
    disposition_label_map_path: str
    call_selection: CallSelectionFilters
    top_n: int
    sample_excerpts_per_reason: int
    fallback_to_ticket_subject: bool
    humanize_reason_codes: bool
    disposition_fallback_humanize: bool
    llm: LlmRecommendationConfig


DEFAULT_LLM_CONFIG = LlmRecommendationConfig(
    enabled=False,
    top_reasons=5,
    transcripts_per_reason=3,
    max_transcript_chars=1200,
    max_summary_chars=400,
)


DEFAULT_SUMMARY_CONFIG = SummaryAnalysisConfig(
    call_reason_fields=DEFAULT_CALL_REASON_FIELDS,
    disposition_fields=DEFAULT_DISPOSITION_FIELDS,
    disposition_label_map_path="config/disposition_label_map.json",
    call_selection=DEFAULT_CALL_SELECTION,
    top_n=15,
    sample_excerpts_per_reason=2,
    fallback_to_ticket_subject=False,
    humanize_reason_codes=True,
    disposition_fallback_humanize=True,
    llm=DEFAULT_LLM_CONFIG,
)


def resolve_summary_config_path(configured_path: Path) -> Path:
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def load_summary_config(
    path: Path,
    *,
    selection_overrides: CallSelectionOverrides | None = None,
) -> SummaryAnalysisConfig:
    path = resolve_summary_config_path(path)
    if not path.is_file():
        config = DEFAULT_SUMMARY_CONFIG
    else:
        raw = json.loads(path.read_text(encoding="utf-8"))
        config = SummaryAnalysisConfig(
            call_reason_fields=_parse_reason_fields(raw.get("call_reason_fields"))
            or DEFAULT_CALL_REASON_FIELDS,
            disposition_fields=_parse_column_list(raw.get("disposition_fields"))
            or DEFAULT_DISPOSITION_FIELDS,
            call_selection=_load_call_selection(raw),
            top_n=int(raw.get("top_n", DEFAULT_SUMMARY_CONFIG.top_n)),
            sample_excerpts_per_reason=int(
                raw.get(
                    "sample_excerpts_per_reason",
                    DEFAULT_SUMMARY_CONFIG.sample_excerpts_per_reason,
                )
            ),
            fallback_to_ticket_subject=bool(
                raw.get(
                    "fallback_to_ticket_subject",
                    DEFAULT_SUMMARY_CONFIG.fallback_to_ticket_subject,
                )
            ),
            humanize_reason_codes=bool(
                raw.get("humanize_reason_codes", DEFAULT_SUMMARY_CONFIG.humanize_reason_codes)
            ),
            disposition_label_map_path=str(
                raw.get(
                    "disposition_label_map_path",
                    DEFAULT_SUMMARY_CONFIG.disposition_label_map_path,
                )
            ).strip(),
            disposition_fallback_humanize=bool(
                raw.get(
                    "disposition_fallback_humanize",
                    DEFAULT_SUMMARY_CONFIG.disposition_fallback_humanize,
                )
            ),
            llm=_parse_llm_config(raw.get("llm_recommendations")),
        )

    if selection_overrides:
        return replace(
            config,
            call_selection=apply_call_selection_overrides(
                config.call_selection,
                selection_overrides,
            ),
        )
    return config


def _load_call_selection(raw: dict) -> CallSelectionFilters:
    block = raw.get("call_selection")
    if isinstance(block, dict):
        return _parse_call_selection_block(block)

    inbound_only = bool(raw.get("inbound_only", True))
    return CallSelectionFilters(
        call_direction="inbound" if inbound_only else "all",
        link_methods=_parse_link_methods(raw.get("matched_link_methods")),
        include_unmatched=bool(raw.get("include_unmatched", False)),
    )


def _parse_call_selection_block(raw: dict) -> CallSelectionFilters:
    call_direction = str(
        raw.get("call_direction", DEFAULT_CALL_SELECTION.call_direction)
    ).strip().lower()
    if call_direction == "inbound_only":
        call_direction = "inbound"

    link_methods_raw = raw.get("link_methods", raw.get("matched_link_methods"))
    if link_methods_raw is None and "link_methods" not in raw and "matched_link_methods" not in raw:
        link_methods = DEFAULT_CALL_SELECTION.link_methods
    else:
        link_methods = _parse_link_methods(link_methods_raw)

    return CallSelectionFilters(
        call_direction=call_direction or DEFAULT_CALL_SELECTION.call_direction,
        skills_include=_parse_string_set(raw.get("skills", raw.get("skills_include"))),
        skills_exclude=_parse_string_set(raw.get("skills_exclude")),
        teams_include=_parse_string_set(raw.get("teams", raw.get("teams_include"))),
        teams_exclude=_parse_string_set(raw.get("teams_exclude")),
        media_types_include=_parse_string_set(
            raw.get("media_types", raw.get("media_types_include"))
        ),
        media_types_exclude=_parse_string_set(raw.get("media_types_exclude")),
        link_methods=link_methods,
        include_unmatched=bool(
            raw.get("include_unmatched", DEFAULT_CALL_SELECTION.include_unmatched)
        ),
    )


def _parse_string_set(entries: object) -> frozenset[str]:
    if not isinstance(entries, list):
        return frozenset()
    return frozenset(str(item).strip() for item in entries if str(item).strip())


def _parse_llm_config(raw: object) -> LlmRecommendationConfig:
    if not isinstance(raw, dict):
        return DEFAULT_LLM_CONFIG
    return LlmRecommendationConfig(
        enabled=bool(raw.get("enabled", DEFAULT_LLM_CONFIG.enabled)),
        top_reasons=int(raw.get("top_reasons", DEFAULT_LLM_CONFIG.top_reasons)),
        transcripts_per_reason=int(
            raw.get("transcripts_per_reason", DEFAULT_LLM_CONFIG.transcripts_per_reason)
        ),
        max_transcript_chars=int(
            raw.get("max_transcript_chars", DEFAULT_LLM_CONFIG.max_transcript_chars)
        ),
        max_summary_chars=int(
            raw.get("max_summary_chars", DEFAULT_LLM_CONFIG.max_summary_chars)
        ),
    )


def _parse_reason_fields(entries: object) -> tuple[ReasonField, ...] | None:
    if not isinstance(entries, list) or not entries:
        return None
    fields: list[ReasonField] = []
    for entry in entries:
        if isinstance(entry, str):
            column = entry.strip()
            if column:
                fields.append(ReasonField(column, column))
            continue
        if not isinstance(entry, dict):
            continue
        column = str(entry.get("column", "")).strip()
        if not column:
            continue
        label = str(entry.get("label") or column).strip()
        fields.append(ReasonField(column, label))
    return tuple(fields) if fields else None


def _parse_column_list(entries: object) -> tuple[str, ...] | None:
    if not isinstance(entries, list) or not entries:
        return None
    columns = tuple(str(item).strip() for item in entries if str(item).strip())
    return columns or None


def _parse_link_methods(entries: object) -> frozenset[str] | None:
    if entries is None:
        return DEFAULT_CALL_SELECTION.link_methods
    if not isinstance(entries, list):
        return None
    if not entries:
        return frozenset()
    return frozenset(str(item).strip() for item in entries if str(item).strip())
