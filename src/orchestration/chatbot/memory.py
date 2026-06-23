"""Conversation memory for the analytics chatbot.

Keeps an ongoing conversation's context so the assistant can build on prior turns
until the conversation is ended (a new session starts). Memory has two parts:

- ``turns``: the full list of (user, assistant) exchanges in order.
- ``summary``: a rolling, compact "running knowledge" of older turns that have
  scrolled out of the verbatim recent window, so long conversations stay within
  a bounded token budget while still accumulating context.

This module is intentionally free of any LLM/network calls so it is cheap and
easy to test. The agent owns refreshing ``summary`` via the language model.
"""

from __future__ import annotations

from dataclasses import dataclass, field

_MAX_ASSISTANT_CHARS = 800
_MAX_CONTEXT_QUESTIONS = 3


@dataclass
class ConversationMemory:
    """Mutable memory for a single, in-progress conversation."""

    turns: list[tuple[str, str]] = field(default_factory=list)
    summary: str = ""
    max_recent_turns: int = 6

    @classmethod
    def from_pairs(
        cls,
        pairs: list[tuple[str, str]] | None,
        *,
        max_recent_turns: int = 6,
        summary: str = "",
    ) -> "ConversationMemory":
        memory = cls(max_recent_turns=max_recent_turns, summary=summary)
        for user_msg, assistant_msg in pairs or []:
            memory.turns.append((str(user_msg), str(assistant_msg)))
        return memory

    def recent_turns(self) -> list[tuple[str, str]]:
        if self.max_recent_turns <= 0:
            return []
        return self.turns[-self.max_recent_turns :]

    def overflow_turn(self) -> tuple[str, str] | None:
        """The turn that just scrolled out of the recent window (to fold into summary)."""
        if self.max_recent_turns <= 0:
            return self.turns[-1] if self.turns else None
        if len(self.turns) > self.max_recent_turns:
            return self.turns[-(self.max_recent_turns + 1)]
        return None

    def add_turn(self, user_message: str, assistant_message: str) -> None:
        self.turns.append((str(user_message), str(assistant_message)))

    def has_context(self) -> bool:
        return bool(self.summary.strip()) or bool(self.turns)

    def format_recent(self) -> str:
        recent = self.recent_turns()
        if not recent:
            return "(none)"
        lines: list[str] = []
        for user_msg, assistant_msg in recent:
            trimmed = assistant_msg.strip()
            if len(trimmed) > _MAX_ASSISTANT_CHARS:
                trimmed = f"{trimmed[:_MAX_ASSISTANT_CHARS]}..."
            lines.append(f"User: {user_msg}")
            lines.append(f"Assistant: {trimmed}")
        return "\n".join(lines)

    def as_prompt_context(self) -> str:
        """Block injected into LLM prompts: running summary + verbatim recent turns."""
        if not self.has_context():
            return "(none)"
        sections: list[str] = []
        if self.summary.strip():
            sections.append(f"Conversation memory (earlier context):\n{self.summary.strip()}")
        sections.append(f"Recent turns:\n{self.format_recent()}")
        return "\n\n".join(sections)

    def contextual_query(self, question: str) -> str:
        """Self-contained query for routing/embedding so follow-ups keep their context.

        Combines the rolling summary and the most recent user questions with the
        current question, e.g. "what about outbound?" still embeds/routes against the
        topic established earlier in the conversation.
        """
        parts: list[str] = []
        if self.summary.strip():
            parts.append(self.summary.strip())
        prior_questions = [user_msg.strip() for user_msg, _ in self.recent_turns() if user_msg.strip()]
        if prior_questions:
            parts.extend(prior_questions[-_MAX_CONTEXT_QUESTIONS:])
        parts.append(question.strip())
        return "\n".join(part for part in parts if part)
