#!/usr/bin/env python3
"""Company-login Gradio chatbot for natural-language analytics over PostgreSQL."""

from __future__ import annotations

import sys
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from orchestration.chatbot.agent import ChatbotAgent  # noqa: E402
from orchestration.chatbot.auth import load_chatbot_auth_users  # noqa: E402
from orchestration.chatbot.settings import get_chatbot_settings  # noqa: E402

load_dotenv(ROOT / ".env")

settings = get_chatbot_settings()
agent = ChatbotAgent(settings)
auth_users = load_chatbot_auth_users(settings)

EXAMPLE_QUESTIONS = [
    "What were the top 10 call reasons for inbound calls in the last 7 days?",
    "How many calls per skill last week for LEV Consumer?",
    "What are the top dispositions this month?",
    "Show call volume by day for the last 14 days (inbound only).",
    "Which skills have the highest negative client sentiment rate last week?",
]


def respond(message: str, history: list[dict]) -> str:
    if not message or not message.strip():
        return "Please enter a question about your call or ticket data."

    # Gradio 4+ message format: list of {"role", "content"} dicts
    legacy_history: list[tuple[str, str]] = []
    if history:
        user_buf: str | None = None
        for item in history:
            role = item.get("role")
            content = str(item.get("content", ""))
            if role == "user":
                user_buf = content
            elif role == "assistant" and user_buf is not None:
                legacy_history.append((user_buf, content))
                user_buf = None

    try:
        result = agent.ask(message.strip(), history=legacy_history)
        return result.answer
    except Exception as exc:
        return f"Something went wrong: {exc}. Check DATABASE_URL and OPENAI_API_KEY."


demo = gr.ChatInterface(
    fn=respond,
    title="Contact Center Analytics Assistant",
    description=(
        "Ask questions about call volume, reasons, dispositions, skills, and trends. "
        "Data is queried live from PostgreSQL (Railway). **Company login required.**"
    ),
    examples=EXAMPLE_QUESTIONS,
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
