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

# First-class trend/comparison intent: the user wants a period-over-period change, not a single
# number. Drives a vetted comparison SQL template in the agent.
TREND_COMPARE_PATTERNS = re.compile(
    r"\b("
    r"vs\.?|versus|compared? to|compared with|"
    r"week[\s-]over[\s-]week|wow|month[\s-]over[\s-]month|mom|"
    r"trend|trending|change[ds]?|changing|"
    r"increase[ds]?|decrease[ds]?|grow(th|ing)?|drop(ped|ping)?|rise|risen|fell|fallen|"
    r"since last|than last|from last|prior (week|month|period|quarter)|"
    r"previous (week|month|period|quarter)|year[\s-]over[\s-]year|yoy|"
    r"more than|fewer than|less than|how did .* (change|compare)"
    r")\b",
    re.IGNORECASE,
)

# First-class drill-down intent: the user wants the actual calls/tickets behind a number, not an
# aggregate. Drives a vetted row-level template in the agent.
DRILLDOWN_PATTERNS = re.compile(
    r"\b("
    r"show me|list( the| out)?|which (calls|tickets|interactions|customers|segments)|"
    r"what (are|were) the (calls|tickets|interactions|examples)|"
    r"drill[\s-]?down|behind (this|that|the|these|those)|see the (calls|tickets|interactions)|"
    r"pull (the|up|some)|give me (the|some) (calls|tickets|examples)|"
    r"individual (calls|tickets|records)|sample (calls|tickets|of)|"
    r"specific (calls|tickets|examples)"
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


def detect_intents(question: str) -> set[str]:
    """Detect first-class query intents that warrant a vetted SQL template.

    Returns any of {"trend_compare", "drilldown"}. These are additive to routing — a question
    can be both an aggregate (sql) and a trend_compare, and the agent injects matching guidance.
    """
    intents: set[str] = set()
    if TREND_COMPARE_PATTERNS.search(question):
        intents.add("trend_compare")
    if DRILLDOWN_PATTERNS.search(question):
        intents.add("drilldown")
    return intents
