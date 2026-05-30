from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

import httpx

from orchestration.chatbot.schema_context import build_schema_prompt
from orchestration.chatbot.settings import ChatbotSettings
from orchestration.chatbot.sql_executor import QueryResult, execute_readonly_query, format_results_for_llm
from orchestration.chatbot.sql_guard import validate_sql
from orchestration.db.session import get_engine


@dataclass
class ChatbotResponse:
    answer: str
    sql: str | None = None
    row_count: int | None = None
    error: str | None = None
    debug: dict = field(default_factory=dict)


class ChatbotAgent:
    def __init__(self, settings: ChatbotSettings) -> None:
        self._settings = settings
        self._engine = get_engine(settings.database_url)

    def ask(self, question: str, *, history: list[tuple[str, str]] | None = None) -> ChatbotResponse:
        history = history or []
        sql, sql_error = self._generate_sql(question, history)
        if sql_error and not sql:
            return ChatbotResponse(answer=sql_error, error=sql_error)

        assert sql is not None
        validation = validate_sql(sql, max_limit=self._settings.chatbot_max_rows)
        if not validation.ok:
            return ChatbotResponse(
                answer=f"I could not run that query safely: {validation.error}",
                sql=sql,
                error=validation.error,
            )

        try:
            result = execute_readonly_query(
                self._engine,
                validation.sql,
                max_rows=self._settings.chatbot_max_rows,
                timeout_seconds=self._settings.chatbot_query_timeout_seconds,
            )
        except Exception as exc:
            corrected = self._retry_sql(question, validation.sql, str(exc))
            if corrected:
                validation = validate_sql(corrected, max_limit=self._settings.chatbot_max_rows)
                if validation.ok:
                    try:
                        result = execute_readonly_query(
                            self._engine,
                            validation.sql,
                            max_rows=self._settings.chatbot_max_rows,
                            timeout_seconds=self._settings.chatbot_query_timeout_seconds,
                        )
                        sql = validation.sql
                    except Exception as retry_exc:
                        return ChatbotResponse(
                            answer=f"Query failed: {retry_exc}",
                            sql=validation.sql,
                            error=str(retry_exc),
                        )
                else:
                    return ChatbotResponse(
                        answer=f"Query failed: {exc}",
                        sql=validation.sql,
                        error=str(exc),
                    )
            else:
                return ChatbotResponse(
                    answer=f"Query failed: {exc}",
                    sql=validation.sql,
                    error=str(exc),
                )

        answer = self._summarize(question, validation.sql, result, history)
        if self._settings.chatbot_show_sql:
            answer = f"{answer}\n\n---\n**SQL used:**\n```sql\n{validation.sql}\n```"

        return ChatbotResponse(
            answer=answer,
            sql=validation.sql,
            row_count=result.row_count,
        )

    def _generate_sql(
        self,
        question: str,
        history: list[tuple[str, str]],
    ) -> tuple[str | None, str | None]:
        history_text = _format_history(history)
        prompt = (
            f"{build_schema_prompt()}\n\n"
            f"Conversation so far:\n{history_text}\n\n"
            f"User question: {question}\n\n"
            "Return ONLY a JSON object: "
            '{"sql": "SELECT ...", "reasoning": "brief note"}'
        )
        content = self._chat_completion(
            system="You write safe PostgreSQL SELECT queries for analytics. JSON only.",
            user=prompt,
        )
        parsed = _parse_json_object(content)
        if not parsed or "sql" not in parsed:
            return None, "I could not generate a valid query for that question. Try rephrasing."
        sql = str(parsed["sql"]).strip()
        return sql, None

    def _retry_sql(self, question: str, failed_sql: str, error: str) -> str | None:
        prompt = (
            f"The following SQL failed.\n\nQuestion: {question}\n\n"
            f"SQL:\n{failed_sql}\n\nError: {error}\n\n"
            f"{build_schema_prompt()}\n\n"
            'Return ONLY JSON: {"sql": "corrected SELECT ..."}'
        )
        content = self._chat_completion(
            system="Fix the PostgreSQL SELECT query. JSON only.",
            user=prompt,
        )
        parsed = _parse_json_object(content)
        if parsed and parsed.get("sql"):
            return str(parsed["sql"]).strip()
        return None

    def _summarize(
        self,
        question: str,
        sql: str,
        result: QueryResult,
        history: list[tuple[str, str]],
    ) -> str:
        if result.row_count == 0:
            return (
                "No matching records were found for that question in the selected time range or filters. "
                "Try widening the date range or relaxing skill/direction filters."
            )

        data_preview = format_results_for_llm(result)
        prompt = (
            f"User question: {question}\n\n"
            f"Query returned {result.row_count} row(s)"
            f"{' (truncated)' if result.truncated else ''}.\n\n"
            f"Results JSON:\n{data_preview}\n\n"
            "Write a clear, concise summary for a contact-center manager. "
            "Use bullet points for rankings. Include counts and percentages where useful. "
            "Do not invent data not present in the results. "
            "If dispositions or reasons look like codes, describe them plainly."
        )
        return self._chat_completion(
            system="You summarize contact center analytics for business users.",
            user=prompt,
        )

    def _chat_completion(self, *, system: str, user: str) -> str:
        url = f"{self._settings.openai_base_url.rstrip('/')}/chat/completions"
        payload = {
            "model": self._settings.openai_model,
            "temperature": 0.2,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        }
        with httpx.Client(timeout=self._settings.request_timeout_seconds) as client:
            response = client.post(
                url,
                headers={
                    "Authorization": f"Bearer {self._settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        return str(body["choices"][0]["message"]["content"])


def _format_history(history: list[tuple[str, str]]) -> str:
    if not history:
        return "(none)"
    lines: list[str] = []
    for user_msg, assistant_msg in history[-4:]:
        lines.append(f"User: {user_msg}")
        lines.append(f"Assistant: {assistant_msg[:500]}")
    return "\n".join(lines)


def _parse_json_object(content: str) -> dict | None:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            return None
