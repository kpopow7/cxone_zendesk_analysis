from __future__ import annotations

from orchestration.chatbot.agent import ChatbotAgent, ChatbotResponse, _form_filter_instruction
from orchestration.chatbot.memory import ConversationMemory
from orchestration.chatbot.schema_context import build_schema_prompt
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.chatbot.sql_guard import ALLOWED_RELATIONS, validate_sql


def _settings(**overrides) -> ChatbotSettings:
    base = dict(
        DATABASE_URL="postgresql+psycopg://u:p@localhost:5433/db",
        OPENAI_API_KEY="sk-dummy",
    )
    base.update(overrides)
    return ChatbotSettings(**base)


def test_no_form_types_yields_empty_instruction() -> None:
    assert _form_filter_instruction(None) == ""
    assert _form_filter_instruction([]) == ""
    assert _form_filter_instruction(["", "   "]) == ""


def test_form_filter_instruction_lists_quoted_names() -> None:
    instruction = _form_filter_instruction(["Assist (Internal)", "Consumer"])

    assert "ticket_form_name IN ('Assist (Internal)', 'Consumer')" in instruction
    assert "mandatory" in instruction.lower()


def test_form_filter_instruction_escapes_single_quotes() -> None:
    instruction = _form_filter_instruction(["O'Brien Form"])
    assert "'O''Brien Form'" in instruction


def test_forms_table_is_allowed_for_chatbot_sql() -> None:
    assert "zendesk_ticket_forms" in ALLOWED_RELATIONS
    result = validate_sql(
        "SELECT form_id, name FROM zendesk_ticket_forms ORDER BY name LIMIT 50"
    )
    assert result.ok


def test_form_filter_on_view_passes_guard() -> None:
    result = validate_sql(
        "SELECT ticket_form_name, COUNT(*) FROM analytics_interactions "
        "WHERE ticket_form_name IN ('Assist (Internal)') GROUP BY ticket_form_name"
    )
    assert result.ok


def test_schema_prompt_documents_form_columns() -> None:
    prompt = build_schema_prompt()
    assert "ticket_form_name" in prompt
    assert "Assist (Internal)" in prompt


def test_ask_forwards_form_types(monkeypatch) -> None:
    agent = ChatbotAgent(_settings())
    monkeypatch.setattr(agent, "_maybe_refresh_summary", lambda memory: None)
    captured: dict = {}

    def fake_respond(q, mem, *, form_types=None):
        captured["form_types"] = form_types
        return ChatbotResponse(answer="ok")

    monkeypatch.setattr(agent, "_respond", fake_respond)
    agent.ask("q", memory=ConversationMemory(), form_types=["Assist (Internal)"])

    assert captured["form_types"] == ["Assist (Internal)"]


def test_ask_ignores_form_types_when_disabled(monkeypatch) -> None:
    agent = ChatbotAgent(_settings(CHATBOT_FORM_FILTER_ENABLED=False))
    monkeypatch.setattr(agent, "_maybe_refresh_summary", lambda memory: None)
    captured: dict = {}

    def fake_respond(q, mem, *, form_types=None):
        captured["form_types"] = form_types
        return ChatbotResponse(answer="ok")

    monkeypatch.setattr(agent, "_respond", fake_respond)
    agent.ask("q", memory=ConversationMemory(), form_types=["Assist (Internal)"])

    assert captured["form_types"] is None
