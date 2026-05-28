from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy import Text, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import text

from orchestration.zendesk.field_map import PromotedField, load_promoted_fields, resolve_field_map_path


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def default_promoted_column_names() -> tuple[str, ...]:
    path = resolve_field_map_path(project_root() / "config/zendesk_field_map.json")
    return tuple(field.column for field in load_promoted_fields(path))


PROMOTED_COLUMN_NAMES: tuple[str, ...] = default_promoted_column_names()


def attach_promoted_columns(model_class: type) -> None:
    """Add Text columns to ZendeskTicketRow for each promoted field in the map file."""
    for column_name in PROMOTED_COLUMN_NAMES:
        if hasattr(model_class, column_name):
            continue
        setattr(
            model_class,
            column_name,
            mapped_column(Text, nullable=True),
        )


def coerce_promoted_db_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        parts = [str(item).strip() for item in value if item is not None and str(item).strip()]
        return ", ".join(parts) if parts else None
    text_value = str(value).strip()
    return text_value or None


def ensure_promoted_columns(engine: Engine, column_names: tuple[str, ...] | None = None) -> None:
    """ALTER TABLE to add any promoted columns that exist in the map but not in the database."""
    names = column_names or PROMOTED_COLUMN_NAMES
    if not names:
        return

    inspector = inspect(engine)
    if "zendesk_tickets" not in inspector.get_table_names():
        return

    existing = {column["name"] for column in inspector.get_columns("zendesk_tickets")}
    missing = [name for name in names if name not in existing]
    if not missing:
        return

    with engine.begin() as connection:
        for column_name in missing:
            connection.execute(
                text(
                    f'ALTER TABLE zendesk_tickets '
                    f'ADD COLUMN IF NOT EXISTS "{column_name}" TEXT'
                )
            )
