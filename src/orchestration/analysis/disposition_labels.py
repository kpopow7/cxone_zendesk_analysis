from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DispositionLabelConfig:
    labels: dict[str, str]
    fallback_humanize: bool


_DISPOSITION_PREFIXES = (
    "dispdealer__",
    "dispdealer_",
    "dispconlev_",
    "dispcon_",
    "levdispdealer_",
)


def normalize_disposition_code(value: str) -> str:
    return " ".join(value.split()).strip().lower()


def looks_like_disposition_code(value: str) -> bool:
    lowered = value.lower()
    return lowered.startswith(_DISPOSITION_PREFIXES) or "__" in lowered


def humanize_disposition_code(value: str) -> str:
    """Turn Zendesk disposition codes into readable labels when no map entry exists."""
    text = value.strip()
    lowered = text.lower()
    for prefix in _DISPOSITION_PREFIXES:
        if lowered.startswith(prefix):
            text = text[len(prefix) :]
            break

    text = text.replace("__", " / ")
    text = re.sub(r"_+", " ", text)
    text = " ".join(text.split())

    # Light domain phrasing for common tokens
    replacements = (
        (r"\bts\b", "Technical support"),
        (r"\brep serv\b", "Repair service"),
        (r"\brepserv\b", "Repair service"),
        (r"\bord\b", "Order"),
        (r"\bsvc\b", "Service"),
        (r"\binfo\b", "information"),
        (r"\btoubleshooting\b", "troubleshooting"),
    )
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text.strip().title() if text else value.strip()


def resolve_disposition_label(
    code: str,
    label_map: dict[str, str],
    *,
    fallback_humanize: bool = True,
) -> str:
    normalized = normalize_disposition_code(code)
    if normalized in label_map:
        return label_map[normalized]
    if fallback_humanize and looks_like_disposition_code(code):
        return humanize_disposition_code(code)
    return code.strip()


def resolve_label_map_path(configured_path: Path) -> Path:
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def load_disposition_label_config(path: Path) -> DispositionLabelConfig:
    path = resolve_label_map_path(path)
    if not path.is_file():
        return DispositionLabelConfig(labels={}, fallback_humanize=True)

    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("labels", raw if isinstance(raw, dict) else {})
    labels: dict[str, str] = {}
    if isinstance(entries, dict):
        for key, value in entries.items():
            if key and value is not None and str(value).strip():
                labels[normalize_disposition_code(str(key))] = str(value).strip()

    return DispositionLabelConfig(
        labels=labels,
        fallback_humanize=bool(raw.get("fallback_humanize", True)),
    )
