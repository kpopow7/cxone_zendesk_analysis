from __future__ import annotations

import re
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.engine import Engine

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy


def _normalize_hint(value: str) -> str:
    return " ".join(value.lower().split())


def _match_hints(hint: str, candidates: list[str]) -> list[str]:
    """Return candidates whose normalized form contains the hint token."""
    needle = _normalize_hint(hint)
    if not needle:
        return []
    matches: list[str] = []
    for candidate in candidates:
        hay = _normalize_hint(candidate)
        if needle in hay or hay in needle:
            matches.append(candidate)
    return matches


def _unique_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def resolve_entities(
    *,
    engine: Engine,
    form_hints: list[str] | None = None,
    skill_hints: list[str] | None = None,
    reason_hints: list[str] | None = None,
    known_form_names: list[str] | None = None,
    known_skills: list[str] | None = None,
    taxonomy: ReasonTaxonomy | None = None,
) -> dict[str, Any]:
    """Map fuzzy user terms to exact database values."""
    form_hints = [h for h in (form_hints or []) if str(h).strip()]
    skill_hints = [h for h in (skill_hints or []) if str(h).strip()]
    reason_hints = [h for h in (reason_hints or []) if str(h).strip()]

    if known_form_names is None:
        forms = _load_union_form_names(engine)
    else:
        forms = list(known_form_names)

    if known_skills is None:
        skills = _load_distinct(engine, "skill_name", "analytics_interactions")
    else:
        skills = list(known_skills)

    matched_forms: list[str] = []
    for hint in form_hints:
        matched_forms.extend(_match_hints(hint, forms))
    matched_skills: list[str] = []
    for hint in skill_hints:
        matched_skills.extend(_match_hints(hint, skills))

    canonical_reasons: list[str] = []
    if taxonomy and reason_hints:
        for hint in reason_hints:
            canonical = taxonomy.canonicalize(hint)
            if canonical and canonical not in canonical_reasons:
                canonical_reasons.append(canonical)
            for category in taxonomy.categories:
                if _normalize_hint(hint) in _normalize_hint(category.canonical):
                    if category.canonical not in canonical_reasons:
                        canonical_reasons.append(category.canonical)
                for alias in category.aliases:
                    if _normalize_hint(hint) in _normalize_hint(alias):
                        if category.canonical not in canonical_reasons:
                            canonical_reasons.append(category.canonical)

    return {
        "form_names": _unique_preserve(matched_forms),
        "skill_names": _unique_preserve(matched_skills),
        "canonical_reasons": canonical_reasons,
        "hints": {
            "form_hints": form_hints,
            "skill_hints": skill_hints,
            "reason_hints": reason_hints,
        },
    }


def list_catalog(
    *,
    engine: Engine,
    dimension: str,
    known_form_names: list[str] | None = None,
    known_skills: list[str] | None = None,
    taxonomy: ReasonTaxonomy | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List distinct values for a dimension (form types, skills, media types, reasons)."""
    dim = dimension.strip().lower().replace("-", "_").replace(" ", "_")
    limit = max(1, min(limit, 500))

    if dim in ("form_type", "form_types", "ticket_form", "ticket_form_name", "ticket_form_names"):
        values = known_form_names or _load_union_form_names(engine, limit=limit)
        return {"dimension": "form_types", "values": values[:limit]}

    if dim in ("skill", "skills", "skill_name"):
        values = known_skills or _load_distinct(
            engine, "skill_name", "analytics_interactions", limit=limit
        )
        return {"dimension": "skills", "values": values[:limit]}

    if dim in ("media_type", "media_types", "channel", "channels"):
        values = _load_distinct(engine, "media_type", "analytics_interactions", limit=limit)
        return {"dimension": "media_types", "values": values[:limit]}

    if dim in ("ticket_channel", "ticket_channels", "zendesk_channel", "zendesk_channels"):
        values = _load_distinct(
            engine, "via_channel", "analytics_zendesk_ticket_channels", limit=limit
        )
        return {"dimension": "ticket_channels", "values": values[:limit]}

    if dim in ("canonical_reason", "canonical_reasons", "reason", "reasons"):
        if taxonomy:
            values = [c.canonical for c in taxonomy.categories]
        else:
            values = _load_distinct(
                engine, "call_reason_canonical", "analytics_interactions", limit=limit
            )
        return {"dimension": "canonical_reasons", "values": values[:limit]}

    return {
        "error": (
            f"Unknown dimension: {dimension}. Try form_types, skills, media_types, "
            "ticket_channels, or canonical_reasons."
        ),
        "values": [],
    }


def _load_union_form_names(engine: Engine, *, limit: int = 500) -> list[str]:
    stmt = text(
        """
        SELECT DISTINCT ticket_form_name AS value
        FROM (
            SELECT ticket_form_name FROM analytics_interactions
            WHERE ticket_form_name IS NOT NULL AND TRIM(ticket_form_name) <> ''
            UNION
            SELECT ticket_form_name FROM analytics_zendesk_tickets
            WHERE ticket_form_name IS NOT NULL AND TRIM(ticket_form_name) <> ''
        ) AS forms
        ORDER BY value
        LIMIT :lim
        """
    )
    with engine.connect() as connection:
        return [str(row.value) for row in connection.execute(stmt, {"lim": limit})]


def _load_distinct(
    engine: Engine,
    column: str,
    table: str,
    *,
    limit: int = 500,
) -> list[str]:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", column) or not re.fullmatch(r"[a-z_][a-z0-9_]*", table):
        raise ValueError("Invalid column or table name")
    stmt = text(
        f"SELECT DISTINCT {column} AS value FROM {table} "
        f"WHERE {column} IS NOT NULL AND TRIM({column}) <> '' "
        f"ORDER BY value LIMIT :lim"
    )
    with engine.connect() as connection:
        return [str(row.value) for row in connection.execute(stmt, {"lim": limit})]
