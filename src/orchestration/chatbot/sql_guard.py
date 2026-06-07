from __future__ import annotations

import re
from dataclasses import dataclass

# DML/DDL and dangerous routines. Excludes replace() — a safe PostgreSQL string function
# used in analytics queries (see schema_context inbound filter examples).
FORBIDDEN_KEYWORDS = (
    "insert",
    "update",
    "delete",
    "drop",
    "alter",
    "create",
    "truncate",
    "grant",
    "revoke",
    "copy",
    "execute",
    "merge",
    "attach",
    "detach",
    "vacuum",
    "pg_sleep",
    "pg_read_file",
    "lo_import",
    "dblink",
)

FORBIDDEN_PATTERN = re.compile(
    r"\b(" + "|".join(FORBIDDEN_KEYWORDS) + r")\b",
    re.IGNORECASE,
)

# Stored-procedure CALL only (not identifiers like call_direction).
CALL_PROCEDURE_PATTERN = re.compile(r"\bCALL\s+[a-z_]", re.IGNORECASE)

# Anonymous PL/pgSQL blocks.
DO_BLOCK_PATTERN = re.compile(r"\bDO\s+\$\$", re.IGNORECASE)

ALLOWED_RELATIONS = frozenset(
    {
        "analytics_interactions",
        "analytics_transcript_summaries",
        "combined_interactions",
        "cxone_transcript_analysis",
        "cxone_transcripts",
        "zendesk_tickets",
    }
)

FROM_JOIN_PATTERN = re.compile(
    r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SqlValidationResult:
    ok: bool
    sql: str
    error: str | None = None


class SqlGuardError(ValueError):
    pass


def validate_sql(sql: str, *, max_limit: int = 200) -> SqlValidationResult:
    text = sql.strip().rstrip(";").strip()
    if not text:
        return SqlValidationResult(ok=False, sql=text, error="Empty SQL")

    if ";" in text:
        return SqlValidationResult(ok=False, sql=text, error="Multiple statements are not allowed")

    normalized = text.lstrip()
    if not (normalized.upper().startswith("SELECT") or normalized.upper().startswith("WITH")):
        return SqlValidationResult(ok=False, sql=text, error="Only SELECT queries are allowed")

    forbidden = _find_forbidden_keyword(text)
    if forbidden:
        return SqlValidationResult(
            ok=False,
            sql=text,
            error=f"Forbidden SQL keyword detected: {forbidden}",
        )

    relations = {match.group(1).lower() for match in FROM_JOIN_PATTERN.finditer(text)}
    disallowed = relations - ALLOWED_RELATIONS
    if disallowed:
        return SqlValidationResult(
            ok=False,
            sql=text,
            error=f"Table(s) not allowed: {', '.join(sorted(disallowed))}",
        )

    if not re.search(r"\blimit\s+\d+", text, re.IGNORECASE):
        if not _looks_like_pure_aggregate(text):
            text = f"{text}\nLIMIT {max_limit}"

    text = _cap_limit(text, max_limit)
    return SqlValidationResult(ok=True, sql=text)


def _find_forbidden_keyword(sql: str) -> str | None:
    """Return the first forbidden keyword match, ignoring string literals."""
    scan_text = _strip_string_literals(sql)

    match = FORBIDDEN_PATTERN.search(scan_text)
    if match:
        return match.group(1).lower()

    if CALL_PROCEDURE_PATTERN.search(scan_text):
        return "call"

    if DO_BLOCK_PATTERN.search(scan_text):
        return "do"

    return None


def _strip_string_literals(sql: str) -> str:
    """Remove single-quoted string contents so literal text is not keyword-scanned."""
    return re.sub(r"'(?:''|[^'])*'", "''", sql)


def _looks_like_pure_aggregate(sql: str) -> bool:
    upper = sql.upper()
    return "GROUP BY" in upper or ("COUNT(" in upper and "LIMIT" not in upper)


def _cap_limit(sql: str, max_limit: int) -> str:
    def replacer(match: re.Match[str]) -> str:
        value = int(match.group(1))
        capped = min(value, max_limit)
        return f"LIMIT {capped}"

    return re.sub(r"\bLIMIT\s+(\d+)\b", replacer, sql, flags=re.IGNORECASE)
