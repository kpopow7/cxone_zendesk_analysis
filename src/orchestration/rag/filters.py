"""Extract metadata filters (skill / reason / date) from a natural-language question.

Pure vector search retrieves on embedding similarity alone, so "remake complaints last week"
can surface semantically-similar calls from any skill or month. This module deterministically
pulls structured filters out of the question — a date window, a known skill name, and a canonical
reason — so retrieval can be RESTRICTED to the rows the user actually meant before ranking by
similarity. Everything is best-effort: when nothing is detected the filters are empty and
retrieval behaves exactly as before.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy
from orchestration.analysis.reasons import normalize_reason_key


@dataclass(frozen=True)
class RetrievalFilters:
    skill_name: str | None = None
    canonical_reason: str | None = None
    start: datetime | None = None
    end: datetime | None = None

    @property
    def has_any(self) -> bool:
        return any(
            value is not None
            for value in (self.skill_name, self.canonical_reason, self.start, self.end)
        )

    def describe(self) -> str:
        parts: list[str] = []
        if self.skill_name:
            parts.append(f"skill={self.skill_name}")
        if self.canonical_reason:
            parts.append(f"reason={self.canonical_reason}")
        if self.start is not None:
            parts.append(f"from={self.start.date().isoformat()}")
        if self.end is not None:
            parts.append(f"to={self.end.date().isoformat()}")
        return ", ".join(parts)


def _now(now: datetime | None) -> datetime:
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    return reference


def _day_start(dt: datetime) -> datetime:
    return datetime.combine(dt.date(), time.min, tzinfo=timezone.utc)


def _day_end(dt: datetime) -> datetime:
    return datetime.combine(dt.date(), time.max, tzinfo=timezone.utc).replace(microsecond=999999)


_NUMBER_WORDS = {
    "a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}


def _as_int(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return _NUMBER_WORDS.get(token)


def detect_date_window(
    question: str, *, now: datetime | None = None
) -> tuple[datetime, datetime] | None:
    """Best-effort (start, end) window from common relative-date phrasing, else None."""
    reference = _now(now)
    text = question.lower()

    if re.search(r"\byesterday\b", text):
        day = reference - timedelta(days=1)
        return _day_start(day), _day_end(day)
    if re.search(r"\btoday\b", text):
        return _day_start(reference), _day_end(reference)

    # "last week" / "past week" -> previous calendar week (Mon-Sun UTC), matching the rest of the app.
    if re.search(r"\b(last|past|previous)\s+week\b", text):
        days_since_monday = reference.date().weekday()
        this_monday = reference.date() - timedelta(days=days_since_monday)
        last_monday = this_monday - timedelta(days=7)
        start = datetime.combine(last_monday, time.min, tzinfo=timezone.utc)
        end = _day_end(datetime.combine(last_monday + timedelta(days=6), time.min, tzinfo=timezone.utc))
        return start, end
    if re.search(r"\bthis\s+week\b", text):
        days_since_monday = reference.date().weekday()
        this_monday = reference.date() - timedelta(days=days_since_monday)
        return datetime.combine(this_monday, time.min, tzinfo=timezone.utc), _day_end(reference)

    # "last/past N days|weeks|months" (and singular "last month").
    match = re.search(r"\b(?:last|past|previous)\s+(\w+)\s+(day|days|week|weeks|month|months)\b", text)
    if match:
        count = _as_int(match.group(1))
        unit = match.group(2)
        if count:
            if unit.startswith("day"):
                delta = timedelta(days=count)
            elif unit.startswith("week"):
                delta = timedelta(weeks=count)
            else:  # months (approximate as 30 days)
                delta = timedelta(days=30 * count)
            return reference - delta, reference
    if re.search(r"\b(last|past|previous)\s+month\b", text):
        return reference - timedelta(days=30), reference

    return None


def detect_skill(question: str, known_skills: list[str]) -> str | None:
    """Return the longest known skill name mentioned in the question (case-insensitive)."""
    text = question.lower()
    best: str | None = None
    for skill in known_skills:
        if not skill:
            continue
        if skill.lower() in text:
            if best is None or len(skill) > len(best):
                best = skill
    return best


def detect_canonical_reason(question: str, taxonomy: ReasonTaxonomy | None) -> str | None:
    """Return the canonical reason whose alias is mentioned in the question, else None.

    Only matches real categories (never the fallback label), so an unrelated question does not
    accidentally restrict retrieval.
    """
    if taxonomy is None:
        return None
    key = normalize_reason_key(question)
    for category in taxonomy.categories:
        for alias in category.aliases:
            if alias and alias in key:
                return category.canonical
    return None


def extract_retrieval_filters(
    question: str,
    *,
    known_skills: list[str] | None = None,
    taxonomy: ReasonTaxonomy | None = None,
    now: datetime | None = None,
) -> RetrievalFilters:
    window = detect_date_window(question, now=now)
    start, end = window if window else (None, None)
    return RetrievalFilters(
        skill_name=detect_skill(question, known_skills or []),
        canonical_reason=detect_canonical_reason(question, taxonomy),
        start=start,
        end=end,
    )
