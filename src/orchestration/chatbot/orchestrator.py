from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Callable

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from orchestration.analysis.reason_taxonomy import ReasonTaxonomy
from orchestration.chatbot.openai_client import friendly_openai_error, is_retryable_openai_error
from orchestration.chatbot.agent_state import AgentRunState
from orchestration.chatbot.memory import ConversationMemory
from orchestration.chatbot.responses import ChatbotResponse
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.chatbot.source_router import classify_question_source
from orchestration.chatbot.tool_schemas import PLANNER_SYSTEM_PROMPT, TOOL_DEFINITIONS
from orchestration.chatbot.tools.registry import ToolContext, execute_tool, serialize_tool_result
from sqlalchemy.engine import Engine


@dataclass
class AgentOrchestrator:
    """ReAct-style planner (style B): loop on tool calls, then synthesize."""

    engine: Engine
    settings: ChatbotSettings
    known_skills: Callable[[], list[str]]
    known_form_names: Callable[[], list[str]]
    reason_taxonomy: Callable[[], ReasonTaxonomy]
    chat_completion: Callable[..., str] | None = None

    def run(
        self,
        question: str,
        memory: ConversationMemory,
        *,
        form_types: list[str] | None = None,
    ) -> ChatbotResponse:
        contextual = memory.contextual_query(question) if memory.has_context() else question
        state = AgentRunState(
            question=question,
            memory_context=memory.as_prompt_context(),
            ui_form_types=form_types,
        )
        ctx = ToolContext(
            engine=self.engine,
            settings=self.settings,
            known_skills=self.known_skills,
            known_form_names=self.known_form_names,
            reason_taxonomy=self.reason_taxonomy,
            contextual_question=contextual,
        )

        user_payload = self._build_user_payload(question, memory, form_types)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": PLANNER_SYSTEM_PROMPT},
            {"role": "user", "content": user_payload},
        ]

        last_sql: str | None = None
        max_steps = self.settings.chatbot_agent_max_steps

        for _ in range(max_steps):
            try:
                assistant_message = self._planner_turn(messages)
            except Exception as exc:
                return ChatbotResponse(
                    answer=_format_orchestrator_error(exc),
                    error=str(exc),
                    mode="agent",
                    debug={"steps": state.tool_trace()},
                )

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                break

            messages.append(assistant_message)
            for tool_call in tool_calls:
                fn = tool_call.get("function") or {}
                name = fn.get("name", "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                except json.JSONDecodeError:
                    arguments = {}

                result = execute_tool(name, arguments, ctx, state)
                state.record(name, arguments, result)
                if name == "run_analytics_sql" and result.get("sql"):
                    last_sql = str(result["sql"])

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call.get("id"),
                        "content": serialize_tool_result(result),
                    }
                )

        try:
            answer = self._synthesize(question, memory, state)
        except Exception as exc:
            return ChatbotResponse(
                answer=_format_orchestrator_error(exc),
                error=str(exc),
                mode="agent",
                sql=last_sql,
                debug={"steps": state.tool_trace()},
            )

        row_count = _max_row_count(state)
        if self.settings.chatbot_show_sql and last_sql:
            answer = f"{answer}\n\n---\n**SQL used:**\n```sql\n{last_sql}\n```"

        return ChatbotResponse(
            answer=answer,
            sql=last_sql,
            row_count=row_count,
            mode="agent",
            rag_sources=_rag_chunk_count(state),
            debug={"steps": state.tool_trace()},
        )

    def _build_user_payload(
        self,
        question: str,
        memory: ConversationMemory,
        form_types: list[str] | None,
    ) -> str:
        # Deterministic source routing: decide up front whether this is a call, ticket,
        # mixed, or all-channel question so the planner stops defaulting to analytics_interactions.
        route = classify_question_source(
            memory.contextual_query(question) if memory.has_context() else question
        )
        parts = [
            f"User question: {question}",
            f"\nConversation so far:\n{memory.as_prompt_context()}",
            f"\n{route.directive}",
        ]
        if form_types:
            quoted = ", ".join(repr(n) for n in form_types)
            parts.append(
                f"\nACTIVE UI FORM FILTER (mandatory): restrict analytics to ticket_form_name IN ({quoted})."
            )
        parts.append("\nUse tools to gather data, then stop calling tools when ready to answer.")
        return "".join(parts)

    def _planner_turn(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.1,
            "messages": messages,
            "tools": TOOL_DEFINITIONS,
            "tool_choice": "auto",
        }
        response = self._post_chat_completion(
            url,
            headers={
                "Authorization": f"Bearer {self.settings.openai_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        body = response.json()
        return body["choices"][0]["message"]

    def _synthesize(
        self,
        question: str,
        memory: ConversationMemory,
        state: AgentRunState,
    ) -> str:
        if not state.tool_results:
            return (
                "I could not gather data for that question. Try rephrasing, widening the date "
                "range, or checking that the pipeline has loaded data and the reduction report "
                "has been built (`run_transcript_summary.py --full-report`)."
            )

        trace = json.dumps(state.tool_trace(), indent=2, default=str)
        if len(trace) > 14000:
            trace = trace[:14000] + "\n... (truncated)"

        prompt = (
            f"Conversation so far:\n{memory.as_prompt_context()}\n\n"
            f"User question: {question}\n\n"
            f"Tool results from the analytics agent:\n{trace}\n\n"
            "Write a clear answer for a contact-center manager. Structure:\n"
            "1) **Answer** — headline findings with counts/percentages from tool results\n"
            "2) **What stands out** — interpret the data; note data limitations if any tool returned 0 rows\n"
            "3) **Takeaway** — actionable next steps or sharper follow-up question\n\n"
            "Rules: Only cite numbers present in tool results. If reduction recommendations "
            "are phone-wide but the question was form-specific, say so. Do not invent data."
        )
        return self._chat_completion_text(
            system=(
                "You are a contact-center analytics partner. Synthesize tool results into "
                "insightful, skimmable answers for business users."
            ),
            user=prompt,
        )

    def _chat_completion_text(self, *, system: str, user: str) -> str:
        if self.chat_completion is not None:
            return self.chat_completion(system=system, user=user)

        url = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self.settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        try:
            response = self._post_chat_completion(
                url,
                headers={
                    "Authorization": f"Bearer {self.settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(friendly_openai_error(exc)) from exc
        body = response.json()
        return str(body["choices"][0]["message"]["content"])

    @retry(
        retry=retry_if_exception(is_retryable_openai_error),
        wait=wait_exponential(multiplier=1, min=2, max=60),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _post_chat_completion(
        self,
        url: str,
        *,
        headers: dict[str, str],
        json: dict,
    ) -> httpx.Response:
        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            response = client.post(url, headers=headers, json=json)
            if response.status_code in (429, 500, 502, 503, 504):
                response.raise_for_status()
            response.raise_for_status()
            return response


def _max_row_count(state: AgentRunState) -> int | None:
    counts = [
        int(r.result.get("row_count", 0))
        for r in state.tool_results
        if r.tool == "run_analytics_sql" and r.result.get("row_count") is not None
    ]
    return max(counts) if counts else None


def _rag_chunk_count(state: AgentRunState) -> int:
    total = 0
    for entry in state.tool_results:
        if entry.tool in ("search_interactions", "search_knowledge"):
            total += int(entry.result.get("chunk_count") or 0)
    return total


def _format_orchestrator_error(exc: Exception) -> str:
    if isinstance(exc, RuntimeError):
        return str(exc)
    return f"Something went wrong: {exc}. Check DATABASE_URL and OPENAI_API_KEY."
