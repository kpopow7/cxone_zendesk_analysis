"""Controlled reason taxonomy: map free-text call reasons onto canonical categories.

Both the transcript-LLM reasons (cxone_transcript_analysis.primary_reason) and the
Zendesk-derived reasons (combined_interactions.call_reason) are free text. That fragments
aggregation — "Order status", "order status check", and "where is my order" all describe the
same thing but rank as three separate reasons. This module loads a small, human-editable
vocabulary (config/reason_taxonomy.json) and deterministically resolves any reason string to a
canonical label so rankings and reconciliation are trustworthy. No LLM call is involved.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from orchestration.analysis.reasons import normalize_reason_key

DEFAULT_FALLBACK_CANONICAL = "Other / Uncategorized"


def reason_key_sql(column: str) -> str:
    """SQL expression that normalizes a reason column to match ``normalize_reason_key``.

    Mirrors the Python normalization (collapse internal whitespace, trim, lowercase) so a
    LEFT JOIN against analytics_reason_taxonomy.reason_key lines up with the keys the builder
    wrote. ``column`` must be a trusted identifier (it is interpolated into SQL).
    """
    return f"lower(btrim(regexp_replace({column}, '\\s+', ' ', 'g')))"

# Reasons that carry no signal should not be forced into a real category.
_EMPTY_REASON_KEYS = frozenset(
    {
        "",
        "(no call reason captured)",
        "none",
        "n/a",
        "na",
        "unknown",
        "null",
    }
)


@dataclass(frozen=True)
class TaxonomyCategory:
    canonical: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class ReasonTaxonomy:
    """An ordered set of canonical categories with case-insensitive alias matching.

    Order matters: the first category whose alias matches a reason wins, so list the
    more specific categories before broad ones in the config.
    """

    categories: tuple[TaxonomyCategory, ...]
    fallback_canonical: str = DEFAULT_FALLBACK_CANONICAL

    def canonicalize(self, reason: str | None) -> str | None:
        """Resolve a free-text reason to its canonical label.

        Returns None for empty/placeholder reasons (so they are excluded from rankings),
        the matched canonical label when an alias hits, or ``fallback_canonical`` otherwise.
        """
        if reason is None:
            return None
        key = normalize_reason_key(str(reason))
        if key in _EMPTY_REASON_KEYS:
            return None
        for category in self.categories:
            for alias in category.aliases:
                if alias and alias in key:
                    return category.canonical
        return self.fallback_canonical

    @property
    def canonical_labels(self) -> tuple[str, ...]:
        return tuple(category.canonical for category in self.categories)


def _default_taxonomy() -> ReasonTaxonomy:
    # An empty taxonomy maps everything to the fallback; the config example ships a real one.
    return ReasonTaxonomy(categories=(), fallback_canonical=DEFAULT_FALLBACK_CANONICAL)


def resolve_taxonomy_path(configured_path: Path) -> Path:
    """Use the configured file, else fall back to the bundled .json.example."""
    if configured_path.is_file():
        return configured_path
    example_path = configured_path.parent / f"{configured_path.stem}.json.example"
    if example_path.is_file():
        return example_path
    return configured_path


def parse_reason_taxonomy(raw: dict) -> ReasonTaxonomy:
    fallback = str(
        raw.get("fallback_canonical", DEFAULT_FALLBACK_CANONICAL)
    ).strip() or DEFAULT_FALLBACK_CANONICAL

    categories: list[TaxonomyCategory] = []
    for entry in raw.get("categories", []):
        if not isinstance(entry, dict):
            continue
        canonical = str(entry.get("canonical", "")).strip()
        if not canonical:
            continue
        aliases_raw = entry.get("aliases", [])
        aliases = tuple(
            normalize_reason_key(str(alias))
            for alias in aliases_raw
            if isinstance(alias, (str, int, float)) and str(alias).strip()
        )
        # Always let a category match its own canonical label, even without an explicit alias.
        canonical_key = normalize_reason_key(canonical)
        if canonical_key and canonical_key not in aliases:
            aliases = (canonical_key, *aliases)
        categories.append(TaxonomyCategory(canonical=canonical, aliases=aliases))

    return ReasonTaxonomy(categories=tuple(categories), fallback_canonical=fallback)


def load_reason_taxonomy(path: Path | str = Path("config/reason_taxonomy.json")) -> ReasonTaxonomy:
    """Load the taxonomy from config, falling back to the .example then an empty taxonomy."""
    resolved = resolve_taxonomy_path(Path(path))
    if not resolved.is_file():
        return _default_taxonomy()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return _default_taxonomy()
    return parse_reason_taxonomy(raw)
