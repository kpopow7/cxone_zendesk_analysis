from __future__ import annotations

import json

from orchestration.chatbot.agent import ChatbotAgent
from orchestration.chatbot.memory import ConversationMemory
from orchestration.chatbot.responses import ChatbotResponse
from orchestration.chatbot.settings import ChatbotSettings


def _settings(**overrides) -> ChatbotSettings:
    base = dict(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5433/db",
        OPENAI_API_KEY="sk-dummy",
        CHATBOT_AGENT_ENABLED=True,
        CHATBOT_SHOW_SQL=False,
    )
    base.update(overrides)
    return ChatbotSettings(**base)


def test_orchestrator_runs_resolve_then_sql(monkeypatch) -> None:
    agent = ChatbotAgent(_settings())
    monkeypatch.setattr(agent, "_maybe_refresh_summary", lambda memory: None)

    planner_calls: list[dict] = []

    def fake_planner_turn(messages):
        step = len(planner_calls)
        planner_calls.append({"messages_len": len(messages)})
        if step == 0:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "resolve_entities",
                            "arguments": json.dumps({"form_hints": ["assist"]}),
                        },
                    }
                ],
            }
        if step == 1:
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_2",
                        "type": "function",
                        "function": {
                            "name": "run_analytics_sql",
                            "arguments": json.dumps(
                                {
                                    "intent": "top_reasons",
                                    "limit": 5,
                                }
                            ),
                        },
                    }
                ],
            }
        return {"role": "assistant", "content": "Ready to answer.", "tool_calls": []}

    monkeypatch.setattr(
        agent._orchestrator_instance(),
        "_planner_turn",
        fake_planner_turn,
    )
    monkeypatch.setattr(
        agent._orchestrator_instance(),
        "_synthesize",
        lambda question, memory, state: "Top Assist reasons summarized.",
    )
    monkeypatch.setattr(
        agent,
        "available_form_types",
        lambda: ["Assist (internal)", "Consumer Support"],
    )
    monkeypatch.setattr(agent, "_known_skills", lambda: [])

    def fake_run_analytics_sql(**kwargs):
        return {
            "sql": "SELECT 1",
            "row_count": 2,
            "rows": [{"reason": "Order status", "call_count": 4}],
            "source": "intent:top_reasons",
        }

    monkeypatch.setattr(
        "orchestration.chatbot.tools.registry.run_analytics_sql",
        fake_run_analytics_sql,
    )

    response = agent.ask(
        "top assist ticket reasons",
        memory=ConversationMemory(),
    )

    assert response.answer == "Top Assist reasons summarized."
    assert response.mode == "agent"
    assert len(planner_calls) >= 2


def test_agent_falls_back_to_legacy_when_disabled(monkeypatch) -> None:
    agent = ChatbotAgent(_settings(CHATBOT_AGENT_ENABLED=False))
    monkeypatch.setattr(agent, "_maybe_refresh_summary", lambda memory: None)
    monkeypatch.setattr(
        agent,
        "_respond_legacy",
        lambda q, mem, **kw: ChatbotResponse(answer="legacy", mode="sql"),
    )
    response = agent.ask("count calls", memory=ConversationMemory())
    assert response.answer == "legacy"
    assert response.mode == "sql"
