from __future__ import annotations

import pytest

from orchestration.chatbot.agent import ChatbotAgent
from orchestration.chatbot.responses import ChatbotResponse
from orchestration.chatbot.memory import ConversationMemory
from orchestration.chatbot.settings import ChatbotSettings


def _settings(**overrides) -> ChatbotSettings:
    base = dict(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5433/db",
        OPENAI_API_KEY="sk-dummy",
    )
    base.update(overrides)
    return ChatbotSettings(**base)


def _agent(monkeypatch, settings: ChatbotSettings) -> ChatbotAgent:
    agent = ChatbotAgent(settings)
    # Avoid any network/DB: stub the core responder and summary refresh.
    monkeypatch.setattr(agent, "_maybe_refresh_summary", lambda memory: None)
    return agent


def test_ask_accumulates_memory_on_success(monkeypatch) -> None:
    agent = _agent(monkeypatch, _settings())
    monkeypatch.setattr(
        agent, "_respond", lambda q, mem, **kw: ChatbotResponse(answer=f"answer to {q}")
    )

    memory = ConversationMemory(max_recent_turns=6)
    agent.ask("first question", memory=memory)
    agent.ask("second question", memory=memory)

    assert memory.turns == [
        ("first question", "answer to first question"),
        ("second question", "answer to second question"),
    ]


def test_ask_does_not_store_error_turns(monkeypatch) -> None:
    agent = _agent(monkeypatch, _settings())
    monkeypatch.setattr(
        agent,
        "_respond",
        lambda q, mem, **kw: ChatbotResponse(answer="boom", error="kaboom"),
    )

    memory = ConversationMemory()
    agent.ask("bad", memory=memory)

    assert memory.turns == []


def test_ask_with_memory_disabled_does_not_accumulate(monkeypatch) -> None:
    agent = _agent(monkeypatch, _settings(CHATBOT_MEMORY_ENABLED=False))
    captured: dict = {}

    def fake_respond(q, mem, **kw):
        captured["memory"] = mem
        return ChatbotResponse(answer="ok")

    monkeypatch.setattr(agent, "_respond", fake_respond)

    memory = ConversationMemory(max_recent_turns=6)
    memory.add_turn("earlier", "context")
    agent.ask("now", memory=memory)

    # Original memory is untouched and a blank, no-context memory was used.
    assert memory.turns == [("earlier", "context")]
    assert captured["memory"].turns == []


def test_ask_seeds_memory_from_history_when_none(monkeypatch) -> None:
    agent = _agent(monkeypatch, _settings())
    seen: dict = {}

    def fake_respond(q, mem, **kw):
        seen["context"] = mem.as_prompt_context()
        return ChatbotResponse(answer="done")

    monkeypatch.setattr(agent, "_respond", fake_respond)

    agent.ask("follow up", history=[("prior q", "prior a")])

    assert "prior q" in seen["context"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
