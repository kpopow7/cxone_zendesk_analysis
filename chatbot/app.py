#!/usr/bin/env python3
"""Company-login Gradio chatbot for natural-language analytics over PostgreSQL."""

from __future__ import annotations

import logging
import sys
import traceback
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.chatbot.agent import ChatbotAgent  # noqa: E402
from orchestration.chatbot.auth import load_chatbot_auth_users  # noqa: E402
from orchestration.chatbot.memory import ConversationMemory  # noqa: E402
from orchestration.chatbot.settings import get_chatbot_settings  # noqa: E402

load_dotenv(ROOT / ".env")

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

settings = get_chatbot_settings()
agent = ChatbotAgent(settings)
auth_users = load_chatbot_auth_users(settings)

EXAMPLE_QUESTIONS = [
    "What were the top 10 call reasons for inbound calls in the last 7 days?",
    "Why are customers calling about remakes on HD Brite? Give examples.",
    "What are customers usually trying to accomplish on remake calls?",
    "How many inbound calls per skill last week?",
    "What patterns do you see in warranty calls and how could we reduce them?",
]


def _history_to_legacy(history: list | None) -> list[tuple[str, str]]:
    """Convert Gradio message history to (user, assistant) pairs."""
    if not history:
        return []

    legacy_history: list[tuple[str, str]] = []

    # Gradio 4/5 messages format: [{"role": "...", "content": "..."}]
    if isinstance(history[0], dict):
        user_buf: str | None = None
        for item in history:
            role = item.get("role")
            content = item.get("content", "")
            if isinstance(content, list):
                text_parts = [
                    part.get("text", "")
                    for part in content
                    if isinstance(part, dict) and part.get("type") == "text"
                ]
                content = " ".join(text_parts).strip()
            content = str(content).strip()
            if role == "user":
                user_buf = content
            elif role == "assistant" and user_buf is not None:
                legacy_history.append((user_buf, content))
                user_buf = None
        return legacy_history

    # Legacy tuple/list pairs: [[user, assistant], ...]
    for item in history:
        if isinstance(item, (list, tuple)) and len(item) == 2:
            legacy_history.append((str(item[0]), str(item[1])))
    return legacy_history


def respond(
    message: str,
    history: list | None,
    memory_state: dict | None,
    form_types: list[str] | None = None,
) -> str:
    if not message or not str(message).strip():
        return "Please enter a question about your call or ticket data."

    # memory_state persists per browser session (the active conversation). It holds a
    # ConversationMemory that accumulates context until the conversation is ended
    # (page reload / new session). Seed it from the UI history on first use.
    if memory_state is None:
        memory_state = {}
    memory = memory_state.get("memory")
    if memory is None:
        memory = ConversationMemory.from_pairs(
            _history_to_legacy(history),
            max_recent_turns=settings.chatbot_memory_max_turns,
        )
        memory_state["memory"] = memory

    try:
        result = agent.ask(
            str(message).strip(),
            memory=memory,
            form_types=[t for t in (form_types or []) if t] or None,
        )
        answer = (result.answer or "").strip()
        if not answer:
            return (
                "The chatbot returned an empty response. Check Railway deploy logs and verify "
                "DATABASE_URL, OPENAI_API_KEY, and that combined_interactions has data."
            )
        return answer
    except Exception as exc:
        logger.error("Chatbot request failed: %s", exc)
        logger.debug(traceback.format_exc())
        return f"Something went wrong: {exc}. Check DATABASE_URL and OPENAI_API_KEY."


# Available ticket form types for the filter control (queried once at startup).
FORM_TYPE_CHOICES: list[str] = (
    agent.available_form_types() if settings.chatbot_form_filter_enabled else []
)

with gr.Blocks(title="Contact Center Analytics Assistant") as demo:
    # Per-session memory store; resets when the conversation/session ends.
    memory_state = gr.State()
    additional_inputs: list = [memory_state]

    if FORM_TYPE_CHOICES:
        form_filter = gr.CheckboxGroup(
            choices=FORM_TYPE_CHOICES,
            value=[],
            label="Ticket form types",
            info="Limit answers to these Zendesk form types. Leave empty to include all forms.",
        )
        additional_inputs.append(form_filter)

    gr.ChatInterface(
        fn=respond,
        type="messages",
        title="Contact Center Analytics Assistant",
        description=(
            "Ask about call volume, reasons, dispositions, skills, trends, and contextual "
            "questions about what customers are calling about (semantic search over call summaries). "
            "Use the ticket form-type filter to focus on specific Zendesk forms (e.g. 'Assist (Internal)'). "
            "The assistant remembers the current conversation, so you can ask follow-ups that "
            "build on earlier questions. Data is queried live from PostgreSQL (Railway). "
            "**Company login required.**"
        ),
        additional_inputs=additional_inputs,
        examples=[[question] + [None] * len(additional_inputs) for question in EXAMPLE_QUESTIONS],
        cache_examples=False,
    )

if __name__ == "__main__":
    demo.launch(
        server_name="0.0.0.0",
        server_port=settings.port,
        auth=auth_users,
        auth_message="Company login required",
        show_error=True,
    )
