from orchestration.chatbot.source_router import classify_question_source


def test_call_questions_route_to_calls() -> None:
    for q in [
        "What are the top call reasons last week?",
        "Why are customers calling about remakes?",
        "Show me phone call transcripts for HD Brite",
    ]:
        route = classify_question_source(q)
        assert route.source == "call", q
        assert "analytics_interactions" in route.directive


def test_ticket_questions_route_to_tickets() -> None:
    for q in [
        "How many email tickets did we get last week?",
        "Ticket volume by channel this month",
        "Show me recent chat tickets",
        "Break down tickets by form type",
    ]:
        route = classify_question_source(q)
        assert route.source == "ticket", q
        assert "analytics_zendesk_ticket_channels" in route.directive
        assert "Do NOT use analytics_interactions" in route.directive


def test_mixed_questions_route_to_both() -> None:
    route = classify_question_source(
        "How many calls vs tickets did we handle last week?"
    )
    assert route.source == "mixed"
    assert "analytics_interactions" in route.directive
    assert "analytics_zendesk_ticket_channels" in route.directive


def test_general_volume_defaults_to_all_channel() -> None:
    for q in [
        "What was our total contact volume last week?",
        "How many interactions overall?",
        "What are we seeing this month?",
    ]:
        route = classify_question_source(q)
        assert route.source == "all_channel", q
        assert "analytics_zendesk_ticket_channels" in route.directive


def test_ambiguous_defaults_to_all_channel_not_calls() -> None:
    route = classify_question_source("anything about blinds")
    assert route.source == "all_channel"
    assert "analytics_interactions" not in route.directive.split(".")[0]
