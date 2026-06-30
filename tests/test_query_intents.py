from orchestration.chatbot.agent import _intent_instruction
from orchestration.rag.router import detect_intents


def test_detect_trend_compare() -> None:
    assert "trend_compare" in detect_intents("How did remake calls change vs last week?")
    assert "trend_compare" in detect_intents("call volume this week compared to last week")
    assert "trend_compare" in detect_intents("week-over-week trend in escalations")
    assert "trend_compare" in detect_intents("did order status calls increase since last month")


def test_detect_drilldown() -> None:
    assert "drilldown" in detect_intents("show me the calls behind that number")
    assert "drilldown" in detect_intents("which calls drove the remake spike")
    assert "drilldown" in detect_intents("list the tickets for order status")
    assert "drilldown" in detect_intents("give me some calls about installation")


def test_detect_both_intents() -> None:
    intents = detect_intents("show me the calls behind the week-over-week increase")
    assert intents == {"trend_compare", "drilldown"}


def test_detect_no_intent() -> None:
    assert detect_intents("how many inbound calls last week") == set()
    assert detect_intents("why do customers call about remakes") == set()


def test_intent_instruction_includes_templates() -> None:
    trend = _intent_instruction("how did calls change vs last week")
    assert "TREND / COMPARISON INTENT" in trend
    assert "prior_count" in trend

    drill = _intent_instruction("show me the calls behind this")
    assert "DRILL-DOWN INTENT" in drill
    assert "no GROUP BY" in drill


def test_intent_instruction_empty_when_no_intent() -> None:
    assert _intent_instruction("how many calls last week") == ""
