from __future__ import annotations

from orchestration.chatbot.memory import ConversationMemory


def test_from_pairs_seeds_turns() -> None:
    memory = ConversationMemory.from_pairs([("hi", "hello"), ("q2", "a2")])

    assert memory.turns == [("hi", "hello"), ("q2", "a2")]
    assert memory.has_context() is True


def test_recent_turns_respects_window() -> None:
    memory = ConversationMemory(max_recent_turns=2)
    for i in range(4):
        memory.add_turn(f"q{i}", f"a{i}")

    assert memory.recent_turns() == [("q2", "a2"), ("q3", "a3")]


def test_overflow_turn_is_the_one_leaving_the_window() -> None:
    memory = ConversationMemory(max_recent_turns=2)
    memory.add_turn("q0", "a0")
    memory.add_turn("q1", "a1")
    assert memory.overflow_turn() is None

    memory.add_turn("q2", "a2")
    # window now holds q1,q2 -> q0 just scrolled out
    assert memory.overflow_turn() == ("q0", "a0")


def test_as_prompt_context_includes_summary_and_recent() -> None:
    memory = ConversationMemory(max_recent_turns=2, summary="- discussed remakes last 7 days")
    memory.add_turn("how many?", "120 calls")

    context = memory.as_prompt_context()

    assert "discussed remakes" in context
    assert "how many?" in context
    assert "120 calls" in context


def test_as_prompt_context_empty() -> None:
    assert ConversationMemory().as_prompt_context() == "(none)"


def test_contextual_query_includes_prior_questions_and_summary() -> None:
    memory = ConversationMemory(max_recent_turns=3, summary="topic: warranty calls")
    memory.add_turn("why do customers call about warranties?", "Because ...")

    enriched = memory.contextual_query("what about for outbound?")

    assert "topic: warranty calls" in enriched
    assert "why do customers call about warranties?" in enriched
    assert enriched.strip().endswith("what about for outbound?")


def test_contextual_query_without_history_is_just_question() -> None:
    memory = ConversationMemory()
    assert memory.contextual_query("hello") == "hello"


def test_zero_window_keeps_no_recent_turns() -> None:
    memory = ConversationMemory(max_recent_turns=0)
    memory.add_turn("q", "a")

    assert memory.recent_turns() == []
    # with no recent window, the latest turn is the overflow candidate to summarize
    assert memory.overflow_turn() == ("q", "a")
