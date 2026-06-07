from orchestration.rag.documents import build_call_interaction_document
from orchestration.rag.router import route_question


def test_build_call_interaction_document() -> None:
    document = build_call_interaction_document(
        {
            "segment_id": "abc-123",
            "interaction_start": "2026-05-28T14:00:00+00:00",
            "call_direction": "IN_BOUND",
            "media_type": "PhoneCall",
            "skill_name": "HD Brite",
            "primary_reason": "Remake order",
            "secondary_reason": "Place new remake order",
            "transcript_summary": "Customer requested a remake for damaged blinds.",
            "ticket_subject": "Remake request",
            "disposition_label": "Order placed",
            "transcript_preview": "Agent: How can I help? Customer: I need a remake.",
        }
    )
    assert document is not None
    assert document.chunk_id == "abc-123"
    assert "Remake order" in document.content
    assert "Customer requested a remake" in document.content
    assert document.metadata["skill_name"] == "HD Brite"


def test_route_question_sql() -> None:
    assert route_question("How many inbound calls last week?") == "sql"


def test_route_question_rag() -> None:
    assert route_question("Why are customers calling about remakes?") == "rag"


def test_route_question_hybrid() -> None:
    assert (
        route_question("How many remake calls last week and why do customers call?")
        == "hybrid"
    )
