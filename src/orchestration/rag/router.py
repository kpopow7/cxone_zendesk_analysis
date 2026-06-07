from __future__ import annotations

import re

AGGREGATE_PATTERNS = re.compile(
    r"\b("
    r"how many|count|volume|total|percentage|percent|trend|per day|per week|"
    r"group by|top \d+|rank|ranking|breakdown|distribution|compare|vs\.?"
    r")\b",
    re.IGNORECASE,
)

CONTEXT_PATTERNS = re.compile(
    r"\b("
    r"why|what happened|describe|example|examples|tell me about|similar|help with|"
    r"issue with|complaint|policy|how do customers|what are customers|"
    r"common problems|root cause|reduce|deflect|self-service|"
    r"what did (they|the customer|callers)|summarize calls about"
    r")\b",
    re.IGNORECASE,
)


def route_question(question: str) -> str:
    """Return sql, rag, or hybrid."""
    aggregate = bool(AGGREGATE_PATTERNS.search(question))
    contextual = bool(CONTEXT_PATTERNS.search(question))

    if aggregate and contextual:
        return "hybrid"
    if contextual:
        return "rag"
    return "sql"
