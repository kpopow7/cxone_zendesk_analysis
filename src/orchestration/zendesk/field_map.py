from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PromotedField:
    field_id: int
    column: str


def slugify_field_title(title: str, *, field_id: int) -> str:
    normalized = title.lower().strip()
    normalized = re.sub(r"[^\w\s-]", "", normalized)
    normalized = re.sub(r"[\s_-]+", "_", normalized).strip("_")
    return normalized or f"field_{field_id}"


def resolve_field_map_path(configured_path: Path) -> Path:
    """Resolve field map path; fall back to .example when the primary file is missing."""
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def load_promoted_fields(path: Path) -> list[PromotedField]:
    path = resolve_field_map_path(path)
    if not path.is_file():
        return []

    raw = json.loads(path.read_text(encoding="utf-8"))
    entries = raw.get("promoted_fields", raw if isinstance(raw, list) else [])
    promoted: list[PromotedField] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        field_id = entry.get("field_id")
        column = entry.get("column")
        if field_id is None or not column:
            continue
        promoted.append(PromotedField(field_id=int(field_id), column=str(column).strip()))
    return promoted


def suggested_column_name(title: str, *, field_id: int) -> str:
    slug = slugify_field_title(title, field_id=field_id)
    if slug.startswith("cf_"):
        return slug
    return f"cf_{slug}"
